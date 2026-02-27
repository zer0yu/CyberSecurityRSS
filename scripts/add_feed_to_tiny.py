#!/usr/bin/env python3
"""Interactively add a discovered RSS feed into tiny.opml."""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; CyberSecurityRSSBot/1.0; "
    "+https://github.com/zer0yu/CyberSecurityRSS)"
)
MAX_FEED_BYTES = 2 * 1024 * 1024


class OpmlStructureError(ValueError):
    """Raised when OPML content does not match expected structure."""


@dataclass(frozen=True)
class FeedMetadata:
    title: str
    html_url: str
    xml_url: str


def normalize_url(url: str) -> str:
    return (url or "").strip()


def is_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def is_rss_outline(node: ET.Element) -> bool:
    return (
        node.tag == "outline"
        and node.attrib.get("type") == "rss"
        and normalize_url(node.attrib.get("xmlUrl", "")) != ""
    )


def get_body(tree: ET.ElementTree, path: Path) -> ET.Element:
    body = tree.getroot().find("body")
    if body is None:
        raise OpmlStructureError(f"{path} does not contain <body>")
    return body


def category_name(node: ET.Element) -> str:
    return (node.attrib.get("title") or node.attrib.get("text") or "").strip()


def list_categories(body: ET.Element) -> List[str]:
    names: List[str] = []
    for child in list(body):
        if child.tag != "outline" or is_rss_outline(child):
            continue
        name = category_name(child)
        if name:
            names.append(name)
    return names


def iter_rss_nodes(parent: ET.Element) -> Iterable[Tuple[ET.Element, ET.Element]]:
    for child in list(parent):
        if child.tag != "outline":
            continue
        if is_rss_outline(child):
            yield parent, child
            continue
        yield from iter_rss_nodes(child)


def build_category_map(body: ET.Element) -> Dict[str, ET.Element]:
    mapping: Dict[str, ET.Element] = {}
    for child in list(body):
        if child.tag != "outline" or is_rss_outline(child):
            continue
        name = category_name(child)
        if name and name not in mapping:
            mapping[name] = child
    return mapping


def resolve_category_name(requested_name: str, category_map: Dict[str, ET.Element]) -> str:
    requested = requested_name.strip()
    for existing in category_map:
        if existing.casefold() == requested.casefold():
            return existing
    return requested


def ensure_category(body: ET.Element, category_map: Dict[str, ET.Element], name: str) -> ET.Element:
    existing = category_map.get(name)
    if existing is not None:
        return existing
    category = ET.Element("outline", {"title": name, "text": name})
    category.text = "\n"
    category.tail = "\n"
    body.append(category)
    category_map[name] = category
    return category


def _find_text_child(node: ET.Element, local_name: str) -> str:
    for child in list(node):
        if strip_namespace(child.tag) != local_name:
            continue
        text = (child.text or "").strip()
        if text:
            return text
    return ""


def _find_first_child(node: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in list(node):
        if strip_namespace(child.tag) == local_name:
            return child
    return None


def _extract_atom_html_url(root: ET.Element) -> str:
    fallback = ""
    for child in list(root):
        if strip_namespace(child.tag) != "link":
            continue
        href = normalize_url(child.attrib.get("href", ""))
        if not href:
            continue
        rel = (child.attrib.get("rel", "alternate") or "alternate").strip().lower()
        if rel in {"alternate", ""}:
            return href
        if not fallback:
            fallback = href
    return fallback


def parse_feed_metadata(xml_bytes: bytes, feed_url: str) -> FeedMetadata:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"RSS/Atom XML parse failed: {exc}") from exc

    root_name = strip_namespace(root.tag)
    title = ""
    html_url = ""

    if root_name == "rss":
        channel = _find_first_child(root, "channel")
        if channel is not None:
            title = _find_text_child(channel, "title")
            html_url = _find_text_child(channel, "link")
    elif root_name == "feed":
        title = _find_text_child(root, "title")
        html_url = _extract_atom_html_url(root)
    elif root_name == "rdf":
        channel = _find_first_child(root, "channel")
        if channel is not None:
            title = _find_text_child(channel, "title")
            html_url = _find_text_child(channel, "link")
    else:
        raise ValueError(f"Unsupported feed root tag: {root_name}")

    safe_title = title or urllib.parse.urlparse(feed_url).netloc or feed_url
    safe_html_url = urllib.parse.urljoin(feed_url, html_url) if html_url else feed_url
    return FeedMetadata(
        title=safe_title,
        html_url=normalize_url(safe_html_url),
        xml_url=normalize_url(feed_url),
    )


def fetch_feed_metadata(feed_url: str, timeout: float) -> FeedMetadata:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }
    request = urllib.request.Request(url=feed_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", response.getcode())
            status_code = int(status) if status is not None else 0
            if status_code >= 400:
                raise ValueError(f"Feed request failed with HTTP {status_code}")
            payload = response.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Feed request failed with HTTP {int(exc.code)}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Feed request failed: {exc.reason}") from exc

    if len(payload) > MAX_FEED_BYTES:
        raise ValueError(f"Feed payload is too large (> {MAX_FEED_BYTES} bytes)")
    return parse_feed_metadata(payload, feed_url=feed_url)


def find_existing_category_for_url(body: ET.Element, xml_url: str) -> Optional[str]:
    wanted = normalize_url(xml_url)
    if not wanted:
        return None
    for parent, rss in iter_rss_nodes(body):
        existing = normalize_url(rss.attrib.get("xmlUrl", ""))
        if existing != wanted:
            continue
        if parent is body:
            return "(top-level)"
        name = category_name(parent)
        return name or "(unnamed)"
    return None


def add_feed_to_tree(
    tree: ET.ElementTree,
    category_name: str,
    metadata: FeedMetadata,
) -> Tuple[bool, str]:
    body = get_body(tree, Path("tiny.opml"))
    existing_category = find_existing_category_for_url(body, metadata.xml_url)
    if existing_category is not None:
        return False, existing_category

    categories = build_category_map(body)
    target_name = resolve_category_name(category_name, categories)
    target = ensure_category(body, categories, target_name)

    feed = ET.Element(
        "outline",
        {
            "type": "rss",
            "title": metadata.title,
            "text": metadata.title,
            "htmlUrl": metadata.html_url,
            "xmlUrl": metadata.xml_url,
        },
    )
    feed.tail = "\n"
    if target.text is None:
        target.text = "\n"
    target.append(feed)
    return True, target_name


def serialize_tree(tree: ET.ElementTree) -> bytes:
    payload = ET.tostring(tree.getroot(), encoding="utf-8", short_empty_elements=True)
    return XML_DECLARATION + payload + b"\n"


def resolve_category_choice(raw_choice: str, categories: List[str]) -> Optional[str]:
    choice = (raw_choice or "").strip()
    if not choice:
        return None
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(categories):
            return categories[index - 1]
        return None
    return choice


def prompt_for_category(categories: List[str]) -> str:
    print("Current categories in tiny.opml:")
    for index, name in enumerate(categories, start=1):
        print(f"{index}. {name}")

    while True:
        raw = input("Choose category number or enter a new category name: ")
        chosen = resolve_category_choice(raw, categories)
        if chosen:
            return chosen
        print("Invalid category selection, please try again.")


def prompt_for_feed_url() -> str:
    while True:
        raw = input("Enter RSS/Atom feed URL: ").strip()
        if is_http_url(raw):
            return raw
        print("Please enter a valid http/https RSS URL.")


def detect_repo_root(start: Path) -> Optional[Path]:
    result = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


def git_pull_ff_only(repo_root: Path) -> Tuple[bool, str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "pull", "--ff-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    output_parts = [part.strip() for part in (result.stdout, result.stderr) if part.strip()]
    output = "\n".join(output_parts)
    return result.returncode == 0, output


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add one RSS/Atom feed into tiny.opml.")
    parser.add_argument("--tiny", default="tiny.opml", help="Path to tiny.opml")
    parser.add_argument("--url", help="RSS/Atom URL; interactive prompt if omitted")
    parser.add_argument("--category", help="Target category; interactive prompt if omitted")
    parser.add_argument("--timeout", type=float, default=12, help="HTTP timeout in seconds")
    parser.add_argument(
        "--no-git-pull",
        action="store_true",
        help="Skip the startup git pull --ff-only step",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    tiny_path = Path(args.tiny)
    if not tiny_path.exists():
        print(f"tiny file not found: {tiny_path}", file=sys.stderr)
        return 1

    if not args.no_git_pull:
        repo_root = detect_repo_root(tiny_path.parent.resolve())
        if repo_root is None:
            print("Not inside a git repository, cannot run git pull --ff-only.", file=sys.stderr)
            return 1
        ok, output = git_pull_ff_only(repo_root)
        if not ok:
            print("git pull --ff-only failed; aborting to avoid drift.", file=sys.stderr)
            if output:
                print(output, file=sys.stderr)
            return 1
        if output:
            print(output)

    feed_url = normalize_url(args.url or prompt_for_feed_url())
    if not is_http_url(feed_url):
        print("Invalid RSS URL; must be http/https.", file=sys.stderr)
        return 1

    try:
        metadata = fetch_feed_metadata(feed_url, timeout=args.timeout)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    tree = ET.parse(tiny_path)
    body = get_body(tree, tiny_path)
    categories = list_categories(body)
    chosen_category = (args.category or "").strip() or prompt_for_category(categories)

    added, target_category = add_feed_to_tree(tree, chosen_category, metadata)
    if not added:
        print(f"Feed already exists in category: {target_category}")
        print(f"RSS: {metadata.xml_url}")
        return 0

    tiny_path.write_bytes(serialize_tree(tree))
    print("Feed added into tiny.opml successfully:")
    print(f"- Category: {target_category}")
    print(f"- Title: {metadata.title}")
    print(f"- Site: {metadata.html_url}")
    print(f"- RSS: {metadata.xml_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
