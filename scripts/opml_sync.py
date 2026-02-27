#!/usr/bin/env python3
"""Auto-clean OPML feeds and sync tiny.opml into CyberSecurityRSS.opml."""

from __future__ import annotations

import argparse
import copy
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
FEED_ROOT_TAGS = {"rss", "feed", "rdf"}
READ_CHUNK_SIZE = 16 * 1024
MAX_PROBE_BYTES = 2 * 1024 * 1024
STATE_VERSION = 1
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; CyberSecurityRSSBot/1.0; "
    "+https://github.com/zer0yu/CyberSecurityRSS)"
)


class OpmlStructureError(ValueError):
    """Raised when the OPML structure is invalid."""


@dataclass
class FeedCheckResult:
    alive: bool
    kind: str
    reason: str
    status_code: Optional[int] = None


@dataclass
class SyncStats:
    checked_urls: int = 0
    alive_urls: int = 0
    dead_urls: int = 0
    hard_fail_urls: int = 0
    transient_fail_urls: int = 0
    dead_removed_tiny: int = 0
    dead_removed_full: int = 0
    duplicates_removed_tiny: int = 0
    duplicates_removed_full: int = 0
    retained_failed_tiny: int = 0
    retained_failed_full: int = 0
    merged_added_full: int = 0
    tiny_links_before: int = 0
    tiny_links_after: int = 0
    full_links_before: int = 0
    full_links_after: int = 0
    tiny_changed: bool = False
    full_changed: bool = False
    state_changed: bool = False

    @property
    def dead_removed_total(self) -> int:
        return self.dead_removed_tiny + self.dead_removed_full

    @property
    def duplicates_removed_total(self) -> int:
        return self.duplicates_removed_tiny + self.duplicates_removed_full

    @property
    def retained_failed_total(self) -> int:
        return self.retained_failed_tiny + self.retained_failed_full

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["dead_removed_total"] = self.dead_removed_total
        data["duplicates_removed_total"] = self.duplicates_removed_total
        data["retained_failed_total"] = self.retained_failed_total
        return data


class IncrementalContentDecoder:
    """Incremental decoder for gzip/deflate content-encoding payloads."""

    def __init__(self, content_encoding: str) -> None:
        self._encoding = (content_encoding or "").lower()
        self._decoder: Optional[zlib.decompressobj] = None
        self._deflate_raw = False

        if "gzip" in self._encoding:
            self._decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif "deflate" in self._encoding:
            self._decoder = zlib.decompressobj()

    def decode(self, chunk: bytes) -> bytes:
        if not self._decoder:
            return chunk
        try:
            return self._decoder.decompress(chunk)
        except zlib.error:
            # Some servers send raw deflate streams without zlib wrapper.
            if "deflate" in self._encoding and not self._deflate_raw:
                self._deflate_raw = True
                self._decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                return self._decoder.decompress(chunk)
            raise

    def flush(self) -> bytes:
        if not self._decoder:
            return b""
        return self._decoder.flush()


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


def first_root_tag_from_response(
    response: Any,
    max_probe_bytes: int,
) -> Optional[str]:
    parser = ET.XMLPullParser(events=("start",))
    decoder = IncrementalContentDecoder(response.headers.get("Content-Encoding", ""))
    total_decoded = 0

    while total_decoded < max_probe_bytes:
        raw = response.read(READ_CHUNK_SIZE)
        if not raw:
            break
        decoded = decoder.decode(raw)
        if not decoded:
            continue
        total_decoded += len(decoded)
        parser.feed(decoded)
        for _, elem in parser.read_events():
            return strip_namespace(elem.tag)

    tail = decoder.flush()
    if tail:
        parser.feed(tail)
        for _, elem in parser.read_events():
            return strip_namespace(elem.tag)
    return None


def classify_http_error(code: int) -> FeedCheckResult:
    if code in {404, 410}:
        return FeedCheckResult(alive=False, kind="hard_fail", reason=f"http_{code}", status_code=code)
    # Treat most HTTP errors as transient to reduce false deletions (WAF/rate-limit/geo blocks).
    return FeedCheckResult(alive=False, kind="transient_fail", reason=f"http_{code}", status_code=code)


def coerce_check_result(result: Any) -> FeedCheckResult:
    if isinstance(result, FeedCheckResult):
        return result
    alive = bool(result)
    if alive:
        return FeedCheckResult(alive=True, kind="alive", reason="mock_alive")
    return FeedCheckResult(alive=False, kind="hard_fail", reason="mock_dead")


class HttpFeedChecker:
    """Checks feed URL reachability and validates RSS/Atom root tag."""

    def __init__(self, timeout: float, retries: int, user_agent: str, max_probe_bytes: int) -> None:
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self.max_probe_bytes = max_probe_bytes

    def __call__(self, url: str) -> FeedCheckResult:
        if not is_http_url(url):
            return FeedCheckResult(alive=False, kind="hard_fail", reason="unsupported_url_scheme")

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            "Accept-Encoding": "gzip, deflate, identity",
        }

        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(url=url, headers=headers, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", response.getcode())
                    status_code = int(status) if status is not None else 0
                    if status_code < 200 or status_code >= 400:
                        return classify_http_error(status_code)

                    root_tag = first_root_tag_from_response(response, self.max_probe_bytes)
                    if root_tag in FEED_ROOT_TAGS:
                        return FeedCheckResult(
                            alive=True,
                            kind="alive",
                            reason="ok",
                            status_code=status_code,
                        )
                    if root_tag:
                        return FeedCheckResult(
                            alive=False,
                            kind="hard_fail",
                            reason=f"non_feed_root:{root_tag}",
                            status_code=status_code,
                        )

                    content_type = (response.headers.get("Content-Type") or "").lower()
                    if "html" in content_type or "json" in content_type:
                        return FeedCheckResult(
                            alive=False,
                            kind="hard_fail",
                            reason=f"non_xml_content_type:{content_type}",
                            status_code=status_code,
                        )
                    # Unknown body shape: avoid destructive delete on first signal.
                    return FeedCheckResult(
                        alive=False,
                        kind="transient_fail",
                        reason="root_tag_not_found",
                        status_code=status_code,
                    )
            except urllib.error.HTTPError as exc:
                result = classify_http_error(int(exc.code))
            except (
                urllib.error.URLError,
                socket.timeout,
                TimeoutError,
                ConnectionError,
                OSError,
                zlib.error,
                ET.ParseError,
            ) as exc:
                result = FeedCheckResult(
                    alive=False,
                    kind="transient_fail",
                    reason=f"network_or_parse_error:{type(exc).__name__}",
                )
            except Exception as exc:  # defensive fallback
                result = FeedCheckResult(
                    alive=False,
                    kind="transient_fail",
                    reason=f"unexpected_error:{type(exc).__name__}",
                )

            if attempt >= self.retries:
                return result
            time.sleep(0.5 * attempt)

        return FeedCheckResult(alive=False, kind="transient_fail", reason="max_retries_exhausted")


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


def load_health_state(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}
    urls = payload.get("urls", {})
    if not isinstance(urls, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for url, meta in urls.items():
        if not isinstance(url, str) or not isinstance(meta, dict):
            continue
        normalized[url] = {
            "hard_failures": int(meta.get("hard_failures", 0) or 0),
            "transient_failures": int(meta.get("transient_failures", 0) or 0),
            "last_reason": str(meta.get("last_reason", "")),
        }
    return normalized


def build_next_health_state(
    urls: Iterable[str],
    check_results: Dict[str, FeedCheckResult],
    previous_state: Dict[str, Dict[str, Any]],
    delete_threshold: int,
) -> Tuple[Dict[str, Dict[str, Any]], Set[str], int, int]:
    now = datetime.now(timezone.utc).isoformat()
    next_state_urls: Dict[str, Dict[str, Any]] = {}
    removable_urls: Set[str] = set()
    hard_fail_urls = 0
    transient_fail_urls = 0

    for url in sorted(set(urls)):
        prev = previous_state.get(url, {})
        prev_hard = int(prev.get("hard_failures", 0) or 0)
        prev_trans = int(prev.get("transient_failures", 0) or 0)
        result = check_results[url]

        if result.alive:
            hard = 0
            trans = 0
        elif result.kind == "hard_fail":
            hard_fail_urls += 1
            hard = prev_hard + 1
            trans = 0
            if hard >= delete_threshold:
                removable_urls.add(url)
        else:
            transient_fail_urls += 1
            hard = 0
            trans = prev_trans + 1

        next_state_urls[url] = {
            "hard_failures": hard,
            "transient_failures": trans,
            "last_reason": result.reason,
            "last_checked_at": now,
        }

    state_payload = {
        "version": STATE_VERSION,
        "updated_at": now,
        "urls": next_state_urls,
    }
    return state_payload, removable_urls, hard_fail_urls, transient_fail_urls


def clean_tree(
    tree: ET.ElementTree,
    path: Path,
    check_results: Dict[str, FeedCheckResult],
    removable_urls: Set[str],
) -> Tuple[int, int, int]:
    body = get_body(tree, path)
    seen_urls: Set[str] = set()
    dead_removed = 0
    dup_removed = 0
    retained_failed = 0

    def visit(parent: ET.Element) -> None:
        nonlocal dead_removed, dup_removed, retained_failed
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

                result = check_results.get(
                    url,
                    FeedCheckResult(alive=False, kind="transient_fail", reason="missing_check_result"),
                )
                should_remove_dead = (not result.alive) and (url in removable_urls)
                if should_remove_dead:
                    parent.remove(child)
                    dead_removed += 1
                    continue

                if not result.alive:
                    retained_failed += 1

                if url in seen_urls:
                    parent.remove(child)
                    dup_removed += 1
                    continue
                seen_urls.add(url)
                continue
            visit(child)

    visit(body)
    return dead_removed, dup_removed, retained_failed


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
    check_results: Dict[str, FeedCheckResult],
) -> int:
    tiny_body = get_body(tiny_tree, tiny_path)
    full_body = get_body(full_tree, full_path)
    full_urls = set(collect_rss_urls(full_body))
    category_map = build_category_map(full_body)
    merged_added = 0

    for source_category, tiny_rss in collect_tiny_entries(tiny_body):
        url = normalize_url(tiny_rss.attrib.get("xmlUrl", ""))
        if not url:
            continue
        if not check_results.get(url, FeedCheckResult(False, "transient_fail", "missing_check_result")).alive:
            continue
        if url in full_urls:
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


def serialize_state(state_payload: Dict[str, Any]) -> bytes:
    return (json.dumps(state_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def check_urls_parallel(
    urls: Iterable[str],
    checker: Callable[[str], Any],
    workers: int,
) -> Dict[str, FeedCheckResult]:
    url_list = sorted(set(urls))
    if not url_list:
        return {}
    max_workers = max(1, min(workers, len(url_list)))
    results: Dict[str, FeedCheckResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(checker, url): url for url in url_list}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                results[url] = coerce_check_result(future.result())
            except Exception as exc:
                results[url] = FeedCheckResult(
                    alive=False,
                    kind="transient_fail",
                    reason=f"checker_exception:{type(exc).__name__}",
                )
    return results


def run_sync(
    tiny_path: Path,
    full_path: Path,
    mode: str,
    fallback_category: str,
    timeout: float,
    retries: int,
    workers: int,
    state_file: Path,
    delete_threshold: int,
    max_probe_bytes: int,
    checker: Optional[Callable[[str], Any]] = None,
) -> Tuple[SyncStats, bool]:
    tiny_path = Path(tiny_path)
    full_path = Path(full_path)
    state_file = Path(state_file)

    if mode not in {"check", "apply"}:
        raise ValueError(f"Unsupported mode: {mode}")

    tiny_original = tiny_path.read_bytes()
    full_original = full_path.read_bytes()
    state_original = state_file.read_bytes() if state_file.exists() else b""

    tiny_tree = ET.parse(tiny_path)
    full_tree = ET.parse(full_path)

    tiny_body = get_body(tiny_tree, tiny_path)
    full_body = get_body(full_tree, full_path)

    stats = SyncStats()
    stats.tiny_links_before = len(collect_rss_urls(tiny_body))
    stats.full_links_before = len(collect_rss_urls(full_body))

    all_urls = set(collect_rss_urls(tiny_body)) | set(collect_rss_urls(full_body))
    check_fn = checker or HttpFeedChecker(
        timeout=timeout,
        retries=retries,
        user_agent=DEFAULT_USER_AGENT,
        max_probe_bytes=max_probe_bytes,
    )
    check_results = check_urls_parallel(all_urls, check_fn, workers=workers)

    stats.checked_urls = len(check_results)
    stats.alive_urls = sum(1 for result in check_results.values() if result.alive)
    stats.dead_urls = stats.checked_urls - stats.alive_urls

    previous_state = load_health_state(state_file)
    next_state_payload, removable_urls, hard_fail_urls, transient_fail_urls = build_next_health_state(
        urls=all_urls,
        check_results=check_results,
        previous_state=previous_state,
        delete_threshold=max(1, delete_threshold),
    )
    stats.hard_fail_urls = hard_fail_urls
    stats.transient_fail_urls = transient_fail_urls

    (
        stats.dead_removed_tiny,
        stats.duplicates_removed_tiny,
        stats.retained_failed_tiny,
    ) = clean_tree(
        tiny_tree,
        tiny_path,
        check_results=check_results,
        removable_urls=removable_urls,
    )

    (
        stats.dead_removed_full,
        stats.duplicates_removed_full,
        stats.retained_failed_full,
    ) = clean_tree(
        full_tree,
        full_path,
        check_results=check_results,
        removable_urls=removable_urls,
    )

    stats.merged_added_full = sync_tiny_to_full(
        tiny_tree=tiny_tree,
        tiny_path=tiny_path,
        full_tree=full_tree,
        full_path=full_path,
        fallback_category=fallback_category,
        check_results=check_results,
    )

    stats.tiny_links_after = len(collect_rss_urls(get_body(tiny_tree, tiny_path)))
    stats.full_links_after = len(collect_rss_urls(get_body(full_tree, full_path)))

    tiny_new = serialize_tree(tiny_tree)
    full_new = serialize_tree(full_tree)
    state_new = serialize_state(next_state_payload)

    stats.tiny_changed = tiny_new != tiny_original
    stats.full_changed = full_new != full_original
    stats.state_changed = state_new != state_original

    changed_for_check = stats.tiny_changed or stats.full_changed
    changed_for_apply = changed_for_check or stats.state_changed

    if mode == "apply":
        if stats.tiny_changed:
            tiny_path.write_bytes(tiny_new)
        if stats.full_changed:
            full_path.write_bytes(full_new)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if stats.state_changed:
            state_file.write_bytes(state_new)

    return stats, (changed_for_check if mode == "check" else changed_for_apply)


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
    parser.add_argument("--state-file", default=".github/opml-health-state.json")
    parser.add_argument("--delete-threshold", type=int, default=2)
    parser.add_argument("--max-probe-bytes", type=int, default=MAX_PROBE_BYTES)
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
            state_file=Path(args.state_file),
            delete_threshold=max(1, args.delete_threshold),
            max_probe_bytes=max(1024, args.max_probe_bytes),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(
        "Summary: checked={checked} alive={alive} dead={dead} hard_fail={hard} transient_fail={transient} "
        "removed_dead={removed_dead} retained_failed={retained_failed} removed_dups={removed_dups} merged_added={merged} "
        "tiny_before={tiny_before} tiny_after={tiny_after} "
        "full_before={full_before} full_after={full_after}".format(
            checked=stats.checked_urls,
            alive=stats.alive_urls,
            dead=stats.dead_urls,
            hard=stats.hard_fail_urls,
            transient=stats.transient_fail_urls,
            removed_dead=stats.dead_removed_total,
            retained_failed=stats.retained_failed_total,
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
