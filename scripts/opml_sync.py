#!/usr/bin/env python3
"""Auto-clean OPML feeds and sync tiny.opml into CyberSecurityRSS.opml."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
FEED_ROOT_TAGS = {"rss", "feed", "rdf"}
MAX_FEED_BYTES = 1024 * 1024
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; CyberSecurityRSSBot/1.0; "
    "+https://github.com/zer0yu/CyberSecurityRSS)"
)


class OpmlStructureError(ValueError):
    """Raised when the OPML structure is invalid."""


@dataclass
class SyncStats:
    checked_urls: int = 0
    alive_urls: int = 0
    dead_urls: int = 0
    dead_removed_tiny: int = 0
    dead_removed_full: int = 0
    duplicates_removed_tiny: int = 0
    duplicates_removed_full: int = 0
    merged_added_full: int = 0
    tiny_links_before: int = 0
    tiny_links_after: int = 0
    full_links_before: int = 0
    full_links_after: int = 0
    tiny_changed: bool = False
    full_changed: bool = False

    @property
    def dead_removed_total(self) -> int:
        return self.dead_removed_tiny + self.dead_removed_full

    @property
    def duplicates_removed_total(self) -> int:
        return self.duplicates_removed_tiny + self.duplicates_removed_full

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["dead_removed_total"] = self.dead_removed_total
        data["duplicates_removed_total"] = self.duplicates_removed_total
        return data


def normalize_url(url: str) -> str:
    return (url or "").strip()


def is_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_rss_outline(element: ET.Element) -> bool:
    return (
        element.tag == "outline"
        and element.attrib.get("type") == "rss"
        and "xmlUrl" in element.attrib
    )


def get_body(tree: ET.ElementTree, path: Path) -> ET.Element:
    body = tree.getroot().find("body")
    if body is None:
        raise OpmlStructureError(f"{path} does not contain <body>")
    return body


def strip_namespace(tag: str) -> str:
    local = tag.rsplit("}", 1)[-1]
    local = local.rsplit(":", 1)[-1]
    return local.lower()


def is_valid_feed_payload(payload: bytes) -> bool:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return False
    return strip_namespace(root.tag) in FEED_ROOT_TAGS


class HttpFeedChecker:
    """Checks feed URL reachability and validates RSS/Atom payload shape."""

    def __init__(self, timeout: float, retries: int, user_agent: str) -> None:
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent

    def __call__(self, url: str) -> bool:
        if not is_http_url(url):
            return False
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        }
        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(url=url, headers=headers, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", response.getcode())
                    if status is None or not (200 <= int(status) < 400):
                        raise urllib.error.HTTPError(
                            url=url,
                            code=int(status) if status is not None else 0,
                            msg="Non-success status code",
                            hdrs=None,
                            fp=None,
                        )
                    payload = response.read(MAX_FEED_BYTES)
                if is_valid_feed_payload(payload):
                    return True
            except Exception:
                if attempt >= self.retries:
                    return False
                time.sleep(0.5 * attempt)
        return False


def iter_rss_nodes(parent: ET.Element) -> Iterable[Tuple[ET.Element, ET.Element]]:
    for child in list(parent):
        if child.tag != "outline":
            continue
        if is_rss_outline(child):
            yield parent, child
            continue
        yield from iter_rss_nodes(child)


def collect_rss_urls(body: ET.Element) -> List[str]:
    urls: List[str] = []
    for _, rss in iter_rss_nodes(body):
        url = normalize_url(rss.attrib.get("xmlUrl", ""))
        if url:
            urls.append(url)
    return urls


def clean_tree(
    tree: ET.ElementTree,
    path: Path,
    alive_by_url: Dict[str, bool],
) -> Tuple[int, int]:
    body = get_body(tree, path)
    seen_urls: Set[str] = set()
    dead_removed = 0
    dup_removed = 0

    def visit(parent: ET.Element) -> None:
        nonlocal dead_removed, dup_removed
        for child in list(parent):
            if child.tag != "outline":
                continue
            if is_rss_outline(child):
                raw = child.attrib.get("xmlUrl", "")
                url = normalize_url(raw)
                if not url:
                    parent.remove(child)
                    dead_removed += 1
                    continue
                if raw != url:
                    child.attrib["xmlUrl"] = url
                if not alive_by_url.get(url, False):
                    parent.remove(child)
                    dead_removed += 1
                    continue
                if url in seen_urls:
                    parent.remove(child)
                    dup_removed += 1
                    continue
                seen_urls.add(url)
                continue
            visit(child)

    visit(body)
    return dead_removed, dup_removed


def top_level_categories(body: ET.Element) -> List[ET.Element]:
    return [
        child
        for child in list(body)
        if child.tag == "outline" and not is_rss_outline(child)
    ]


def category_name(outline: ET.Element) -> str:
    return (outline.attrib.get("title") or outline.attrib.get("text") or "").strip()


def build_category_map(body: ET.Element) -> Dict[str, ET.Element]:
    mapping: Dict[str, ET.Element] = {}
    for category in top_level_categories(body):
        name = category_name(category)
        if name and name not in mapping:
            mapping[name] = category
    return mapping


def ensure_category(
    body: ET.Element,
    category_map: Dict[str, ET.Element],
    name: str,
) -> ET.Element:
    existing = category_map.get(name)
    if existing is not None:
        return existing
    category = ET.Element("outline", {"title": name, "text": name})
    category.text = "\n"
    category.tail = "\n"
    body.append(category)
    category_map[name] = category
    return category


def collect_tiny_entries(tiny_body: ET.Element) -> List[Tuple[str, ET.Element]]:
    entries: List[Tuple[str, ET.Element]] = []
    for top in list(tiny_body):
        if top.tag != "outline":
            continue
        if is_rss_outline(top):
            entries.append(("", top))
            continue
        top_name = category_name(top)
        for _, rss in iter_rss_nodes(top):
            entries.append((top_name, rss))
    return entries


def sync_tiny_to_full(
    tiny_tree: ET.ElementTree,
    tiny_path: Path,
    full_tree: ET.ElementTree,
    full_path: Path,
    fallback_category: str,
) -> int:
    tiny_body = get_body(tiny_tree, tiny_path)
    full_body = get_body(full_tree, full_path)
    full_urls = set(collect_rss_urls(full_body))
    category_map = build_category_map(full_body)
    merged_added = 0

    for source_category, tiny_rss in collect_tiny_entries(tiny_body):
        url = normalize_url(tiny_rss.attrib.get("xmlUrl", ""))
        if not url or url in full_urls:
            continue
        target_name = source_category if source_category in category_map else fallback_category
        target_category = ensure_category(full_body, category_map, target_name)
        copied = copy.deepcopy(tiny_rss)
        copied.attrib["xmlUrl"] = url
        if copied.tail is None:
            copied.tail = "\n"
        if target_category.text is None:
            target_category.text = "\n"
        target_category.append(copied)
        full_urls.add(url)
        merged_added += 1

    return merged_added


def serialize_tree(tree: ET.ElementTree) -> bytes:
    root = tree.getroot()
    payload = ET.tostring(root, encoding="utf-8", short_empty_elements=True)
    return XML_DECLARATION + payload + b"\n"


def check_urls_parallel(
    urls: Iterable[str],
    checker: Callable[[str], bool],
    workers: int,
) -> Dict[str, bool]:
    url_list = sorted(set(urls))
    if not url_list:
        return {}
    max_workers = max(1, min(workers, len(url_list)))
    results: Dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(checker, url): url for url in url_list}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                results[url] = bool(future.result())
            except Exception:
                results[url] = False
    return results


def run_sync(
    tiny_path: Path,
    full_path: Path,
    mode: str,
    fallback_category: str,
    timeout: float,
    retries: int,
    workers: int,
    checker: Optional[Callable[[str], bool]] = None,
) -> Tuple[SyncStats, bool]:
    tiny_path = Path(tiny_path)
    full_path = Path(full_path)
    if mode not in {"check", "apply"}:
        raise ValueError(f"Unsupported mode: {mode}")

    tiny_original = tiny_path.read_bytes()
    full_original = full_path.read_bytes()

    tiny_tree = ET.parse(tiny_path)
    full_tree = ET.parse(full_path)

    tiny_body = get_body(tiny_tree, tiny_path)
    full_body = get_body(full_tree, full_path)

    stats = SyncStats()
    stats.tiny_links_before = len(collect_rss_urls(tiny_body))
    stats.full_links_before = len(collect_rss_urls(full_body))

    all_urls = set(collect_rss_urls(tiny_body)) | set(collect_rss_urls(full_body))
    check_fn = checker or HttpFeedChecker(timeout=timeout, retries=retries, user_agent=DEFAULT_USER_AGENT)
    alive_by_url = check_urls_parallel(all_urls, check_fn, workers=workers)
    stats.checked_urls = len(alive_by_url)
    stats.alive_urls = sum(1 for alive in alive_by_url.values() if alive)
    stats.dead_urls = stats.checked_urls - stats.alive_urls

    stats.dead_removed_tiny, stats.duplicates_removed_tiny = clean_tree(tiny_tree, tiny_path, alive_by_url)
    stats.dead_removed_full, stats.duplicates_removed_full = clean_tree(full_tree, full_path, alive_by_url)
    stats.merged_added_full = sync_tiny_to_full(
        tiny_tree=tiny_tree,
        tiny_path=tiny_path,
        full_tree=full_tree,
        full_path=full_path,
        fallback_category=fallback_category,
    )

    stats.tiny_links_after = len(collect_rss_urls(get_body(tiny_tree, tiny_path)))
    stats.full_links_after = len(collect_rss_urls(get_body(full_tree, full_path)))

    tiny_new = serialize_tree(tiny_tree)
    full_new = serialize_tree(full_tree)
    stats.tiny_changed = tiny_new != tiny_original
    stats.full_changed = full_new != full_original
    changed = stats.tiny_changed or stats.full_changed

    if mode == "apply":
        if stats.tiny_changed:
            tiny_path.write_bytes(tiny_new)
        if stats.full_changed:
            full_path.write_bytes(full_new)

    return stats, changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate RSS links, clean OPML files, and sync tiny.opml into CyberSecurityRSS.opml."
    )
    parser.add_argument("--mode", choices=["check", "apply"], required=True)
    parser.add_argument("--tiny", default="tiny.opml")
    parser.add_argument("--full", default="CyberSecurityRSS.opml")
    parser.add_argument("--fallback-category", default="Misc")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--workers", type=int, default=20)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        stats, changed = run_sync(
            tiny_path=Path(args.tiny),
            full_path=Path(args.full),
            mode=args.mode,
            fallback_category=args.fallback_category,
            timeout=args.timeout,
            retries=max(1, args.retries),
            workers=max(1, args.workers),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(
        "Summary: checked={checked} alive={alive} dead={dead} "
        "removed_dead={removed_dead} removed_dups={removed_dups} merged_added={merged} "
        "tiny_before={tiny_before} tiny_after={tiny_after} "
        "full_before={full_before} full_after={full_after}".format(
            checked=stats.checked_urls,
            alive=stats.alive_urls,
            dead=stats.dead_urls,
            removed_dead=stats.dead_removed_total,
            removed_dups=stats.duplicates_removed_total,
            merged=stats.merged_added_full,
            tiny_before=stats.tiny_links_before,
            tiny_after=stats.tiny_links_after,
            full_before=stats.full_links_before,
            full_after=stats.full_links_after,
        )
    )
    print(json.dumps(stats.to_dict(), ensure_ascii=False, sort_keys=True))

    if args.mode == "check" and changed:
        print(
            "Detected OPML drift: run in apply mode to update files "
            "(or merge to master and let workflow auto-fix).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
