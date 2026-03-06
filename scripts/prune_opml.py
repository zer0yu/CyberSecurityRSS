#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "aiohttp>=3.9",
#   "feedparser>=6.0",
#   "openai>=1.0",
# ]
# ///
"""
Prune tiny.opml by removing:
  1. Dead feeds  – HTTP 4xx/5xx or connection failure on xmlUrl
  2. Stale feeds – no article published on or after 2025-01-01
                   (LLM fallback when dates cannot be parsed)
  3. Low-quality feeds – rated < QUALITY_THRESHOLD by LLM

Output:
  tiny_pruned.opml   – cleaned OPML (original never modified)
  prune_report.json  – full audit trail

Usage:
  uv run scripts/prune_opml.py
  uv run scripts/prune_opml.py --input tiny.opml --output tiny_pruned.opml --model gpt-4o-mini
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import feedparser
from openai import AsyncOpenAI

# ── Constants ────────────────────────────────────────────────────────────────

CUTOFF_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
HTTP_CONCURRENCY = 10   # parallel feed fetches
LLM_CONCURRENCY  = 5    # parallel LLM calls (respect rate-limit)
QUALITY_BATCH    = 5    # feeds per LLM quality-check call
QUALITY_THRESHOLD = 5   # keep feeds scored >= this (out of 10)
HTTP_TIMEOUT     = 25   # seconds
MAX_FEED_BYTES   = 512 * 1024  # 512 KB cap to avoid monster feeds
USER_AGENT       = (
    "Mozilla/5.0 (compatible; OPMLPruner/1.0; "
    "+https://github.com/zer0yu/CyberSecurityRSS)"
)

XML_DECL = b'<?xml version="1.0" encoding="UTF-8"?>\n'

# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class FeedEntry:
    title:    str
    xml_url:  str
    html_url: str
    category: str

@dataclass
class FeedResult:
    title:         str
    xml_url:       str
    category:      str
    verdict:       str   # "keep" | "dead" | "stale" | "low_quality"
    reason:        str   = ""
    http_code:     Optional[int]      = None
    last_updated:  Optional[str]      = None   # ISO-8601
    quality_score: Optional[int]      = None

# ── OPML helpers ─────────────────────────────────────────────────────────────

def parse_opml(path: Path) -> tuple[ET.ElementTree, list[FeedEntry]]:
    tree = ET.parse(path)
    root = tree.getroot()
    body = root.find("body")
    if body is None:
        raise ValueError("OPML has no <body>")
    feeds: list[FeedEntry] = []
    for cat in body:
        if cat.tag != "outline":
            continue
        cat_name = (cat.attrib.get("title") or cat.attrib.get("text") or "").strip()
        for node in cat:
            if node.tag != "outline" or node.attrib.get("type") != "rss":
                continue
            xml_url = (node.attrib.get("xmlUrl") or "").strip()
            if not xml_url:
                continue
            feeds.append(FeedEntry(
                title    = (node.attrib.get("title") or node.attrib.get("text") or xml_url).strip(),
                xml_url  = xml_url,
                html_url = (node.attrib.get("htmlUrl") or "").strip(),
                category = cat_name,
            ))
    return tree, feeds


def build_pruned_opml(original: ET.ElementTree, removed_urls: set[str]) -> bytes:
    """Return serialized OPML bytes with the given xmlUrls removed."""
    import copy
    root = copy.deepcopy(original.getroot())
    body = root.find("body")
    if body is None:
        return XML_DECL + ET.tostring(root, encoding="unicode").encode()

    for cat in list(body):
        if cat.tag != "outline":
            continue
        for node in list(cat):
            if node.tag != "outline":
                continue
            url = (node.attrib.get("xmlUrl") or "").strip()
            if url in removed_urls:
                cat.remove(node)
        # Remove empty categories
        rss_children = [n for n in cat if n.tag == "outline" and n.attrib.get("type") == "rss"]
        if not rss_children:
            body.remove(cat)

    payload = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return XML_DECL + payload + b"\n"

# ── HTTP fetch ────────────────────────────────────────────────────────────────

async def fetch_feed(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    entry: FeedEntry,
) -> tuple[FeedEntry, Optional[int], Optional[bytes]]:
    """
    Returns (entry, http_code, body_bytes).
    http_code=None means a network/timeout error.
    body_bytes=None means the request failed or returned error status.
    """
    async with sem:
        try:
            async with session.get(
                entry.xml_url,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                allow_redirects=True,
                max_redirects=10,
            ) as resp:
                code = resp.status
                if code >= 400:
                    return entry, code, None
                data = await resp.content.read(MAX_FEED_BYTES)
                return entry, code, data
        except aiohttp.TooManyRedirects:
            return entry, 310, None
        except asyncio.TimeoutError:
            return entry, None, None
        except Exception:
            return entry, None, None

# ── Feed parsing ──────────────────────────────────────────────────────────────

def _to_utc(t: time.struct_time | None) -> Optional[datetime]:
    if t is None:
        return None
    try:
        ts = time.mktime(t)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def latest_entry_date(raw: bytes) -> Optional[datetime]:
    """Return the most recent article date found in the feed, or None."""
    parsed = feedparser.parse(raw)
    best: Optional[datetime] = None
    for entry in parsed.entries:
        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            dt = _to_utc(getattr(entry, attr, None))
            if dt and (best is None or dt > best):
                best = dt
    # Also check feed-level updated
    feed_dt = _to_utc(getattr(parsed.feed, "updated_parsed", None))
    if feed_dt and (best is None or feed_dt > best):
        best = feed_dt
    return best

# ── LLM helpers ───────────────────────────────────────────────────────────────

def _make_client(args: argparse.Namespace) -> AsyncOpenAI:
    api_key  = args.api_key  or os.environ.get("OPENAI_API_KEY", "")
    # Support both OPENAI_API_BASE (common in custom deployments) and OPENAI_BASE_URL
    base_url = (
        args.base_url
        or os.environ.get("OPENAI_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def llm_guess_date(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    model: str,
    entry: FeedEntry,
    raw_snippet: str,
) -> Optional[datetime]:
    """
    Ask LLM if the feed has any article published on/after 2025-01-01.
    Returns a rough datetime if yes, None if stale/unknown.
    """
    prompt = (
        "You are analyzing an RSS/Atom feed snippet to determine the most recent article date.\n"
        "Feed title: {title}\n"
        "Feed URL: {url}\n\n"
        "Feed content (first 3000 chars):\n```\n{snippet}\n```\n\n"
        "Task: Find the most recent publication date mentioned in this feed content.\n"
        "Reply with ONLY a JSON object in this exact format (no markdown):\n"
        '{{"has_recent": true/false, "latest_date": "YYYY-MM-DD or null", "reasoning": "brief"}}\n'
        '"has_recent" should be true if any article is from 2025-01-01 or later.'
    ).format(
        title=entry.title[:100],
        url=entry.xml_url,
        snippet=raw_snippet[:3000],
    )
    async with sem:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=200,
            )
            text = (resp.choices[0].message.content or "").strip()
            # Strip markdown fences if present
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            obj = json.loads(text)
            if obj.get("has_recent"):
                date_str = obj.get("latest_date") or "2025-01-01"
                try:
                    return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                except ValueError:
                    return datetime(2025, 1, 1, tzinfo=timezone.utc)
            return None
        except Exception as exc:
            print(f"  [LLM date] error for {entry.xml_url}: {exc}", file=sys.stderr)
            return None  # conservative: treat as unknown → keep for quality check


async def llm_quality_batch(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    model: str,
    batch: list[tuple[FeedEntry, bytes]],
) -> list[tuple[int, str]]:
    """
    Rate a batch of feeds for quality (cybersecurity / tech relevance).
    Returns list of (score 0-10, reason) in same order as batch.
    """
    items_text = ""
    for i, (entry, raw) in enumerate(batch, 1):
        parsed = feedparser.parse(raw)
        feed_desc = (getattr(parsed.feed, "description", "") or getattr(parsed.feed, "subtitle", "") or "")[:300]
        sample_titles = [e.title for e in parsed.entries[:5] if hasattr(e, "title")]
        items_text += (
            f"\n[{i}] Title: {entry.title}\n"
            f"    URL: {entry.xml_url}\n"
            f"    Category: {entry.category}\n"
            f"    Description: {feed_desc[:200]}\n"
            f"    Recent article titles: {json.dumps(sample_titles, ensure_ascii=False)}\n"
        )

    prompt = (
        "You are a curator of a cybersecurity & tech RSS feed list.\n"
        "Rate each feed on quality and relevance (0-10).\n\n"
        "Scoring criteria:\n"
        "- 8-10: High-quality, frequently updated, original security/tech research or deep technical content\n"
        "- 5-7: Decent technical content but may be infrequent or mix unrelated topics\n"
        "- 3-4: Mostly reposted content, low depth, or marginally relevant\n"
        "- 0-2: Spam, dead, off-topic, or essentially empty\n\n"
        f"Feeds to rate:\n{items_text}\n"
        "Reply with ONLY a JSON array (no markdown) with one object per feed in order:\n"
        '[{"score": <int>, "reason": "<brief reason>"}]\n'
        f"The array must have exactly {len(batch)} elements."
    )

    async with sem:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=800,
            )
            text = (resp.choices[0].message.content or "").strip()
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            arr = json.loads(text)
            results = []
            for item in arr[:len(batch)]:
                score  = int(item.get("score", 5))
                reason = str(item.get("reason", ""))
                results.append((score, reason))
            # Pad if LLM returned fewer items
            while len(results) < len(batch):
                results.append((5, "LLM returned incomplete response"))
            return results
        except Exception as exc:
            print(f"  [LLM quality] batch error: {exc}", file=sys.stderr)
            return [(5, f"LLM error: {exc}") for _ in batch]

# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    input_path  = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)

    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    tree, feeds = parse_opml(input_path)
    total = len(feeds)
    print(f"Loaded {total} feeds from {input_path}")

    client    = _make_client(args)
    http_sem  = asyncio.Semaphore(HTTP_CONCURRENCY)
    llm_sem   = asyncio.Semaphore(LLM_CONCURRENCY)

    results: list[FeedResult] = []
    # Map xml_url → raw bytes (for surviving feeds that need quality check)
    raw_cache: dict[str, bytes] = {}

    # ── Phase 1: HTTP liveness check ──────────────────────────────────────────
    print(f"\n[Phase 1] Checking liveness of {total} feeds …")
    connector = aiohttp.TCPConnector(limit=HTTP_CONCURRENCY + 5, ssl=False)
    headers   = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [fetch_feed(session, http_sem, e) for e in feeds]
        done  = 0
        fetch_results: list[tuple[FeedEntry, Optional[int], Optional[bytes]]] = []
        for coro in asyncio.as_completed(tasks):
            entry, code, body = await coro
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  {done}/{total} checked …")
            fetch_results.append((entry, code, body))

    # Sort back to original order for stable output
    url_order = {e.xml_url: i for i, e in enumerate(feeds)}
    fetch_results.sort(key=lambda x: url_order.get(x[0].xml_url, 0))

    dead_count = 0
    live_feeds: list[tuple[FeedEntry, bytes]] = []

    for entry, code, body in fetch_results:
        if body is None:
            # Dead
            reason = f"HTTP {code}" if code else "Connection error / timeout"
            results.append(FeedResult(
                title=entry.title, xml_url=entry.xml_url, category=entry.category,
                verdict="dead", reason=reason, http_code=code,
            ))
            dead_count += 1
        else:
            live_feeds.append((entry, body))

    print(f"  Dead: {dead_count} | Live: {len(live_feeds)}")

    # ── Phase 2: Staleness check ──────────────────────────────────────────────
    print(f"\n[Phase 2] Checking article freshness (cutoff: {CUTOFF_DATE.date()}) …")

    fresh_feeds:    list[tuple[FeedEntry, bytes]] = []
    unknown_feeds:  list[tuple[FeedEntry, bytes]] = []
    stale_count = 0

    for entry, body in live_feeds:
        dt = latest_entry_date(body)
        if dt is None:
            unknown_feeds.append((entry, body))
        elif dt < CUTOFF_DATE:
            results.append(FeedResult(
                title=entry.title, xml_url=entry.xml_url, category=entry.category,
                verdict="stale",
                reason=f"Last article: {dt.date().isoformat()}",
                last_updated=dt.isoformat(),
            ))
            stale_count += 1
        else:
            fresh_feeds.append((entry, body))
            raw_cache[entry.xml_url] = body

    print(f"  Fresh: {len(fresh_feeds)} | Stale: {stale_count} | Unknown date: {len(unknown_feeds)}")

    # LLM fallback for feeds with no parseable dates
    if unknown_feeds:
        print(f"  Asking LLM about {len(unknown_feeds)} undated feeds …")
        date_tasks = []
        for entry, body in unknown_feeds:
            snippet = body[:3000].decode("utf-8", errors="replace")
            date_tasks.append(llm_guess_date(client, llm_sem, args.model, entry, snippet))

        date_guesses = await asyncio.gather(*date_tasks)
        for (entry, body), dt in zip(unknown_feeds, date_guesses):
            if dt is None:
                # LLM says stale or couldn't tell → treat as stale
                results.append(FeedResult(
                    title=entry.title, xml_url=entry.xml_url, category=entry.category,
                    verdict="stale",
                    reason="No parseable dates; LLM found no 2025+ articles",
                ))
                stale_count += 1
            else:
                fresh_feeds.append((entry, body))
                raw_cache[entry.xml_url] = body

    print(f"  Total stale: {stale_count} | Proceeding to quality check: {len(fresh_feeds)}")

    # ── Phase 3: Quality check ────────────────────────────────────────────────
    print(f"\n[Phase 3] LLM quality-rating {len(fresh_feeds)} feeds (batch={QUALITY_BATCH}) …")

    # Batch feeds
    batches: list[list[tuple[FeedEntry, bytes]]] = []
    for i in range(0, len(fresh_feeds), QUALITY_BATCH):
        batches.append(fresh_feeds[i : i + QUALITY_BATCH])

    quality_tasks = [
        llm_quality_batch(client, llm_sem, args.model, batch)
        for batch in batches
    ]

    low_quality_count = 0
    keep_count        = 0

    batch_results = await asyncio.gather(*quality_tasks)
    for batch, scores in zip(batches, batch_results):
        for (entry, body), (score, reason) in zip(batch, scores):
            if score < args.threshold:
                results.append(FeedResult(
                    title=entry.title, xml_url=entry.xml_url, category=entry.category,
                    verdict="low_quality",
                    reason=f"Score {score}/10 – {reason}",
                    quality_score=score,
                ))
                low_quality_count += 1
            else:
                results.append(FeedResult(
                    title=entry.title, xml_url=entry.xml_url, category=entry.category,
                    verdict="keep",
                    reason=f"Score {score}/10 – {reason}",
                    quality_score=score,
                ))
                keep_count += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Total feeds:       {total}")
    print(f"  Kept:              {keep_count}")
    print(f"  Removed (dead):    {dead_count}")
    print(f"  Removed (stale):   {stale_count}")
    print(f"  Removed (quality): {low_quality_count}")
    print(f"{'='*60}")

    # ── Write pruned OPML ─────────────────────────────────────────────────────
    removed_urls = {r.xml_url for r in results if r.verdict != "keep"}
    pruned_bytes = build_pruned_opml(tree, removed_urls)
    output_path.write_bytes(pruned_bytes)
    print(f"\nPruned OPML written to: {output_path}")

    # ── Write report ──────────────────────────────────────────────────────────
    report = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "input":         str(input_path),
        "output":        str(output_path),
        "model":         args.model,
        "quality_threshold": args.threshold,
        "cutoff_date":   CUTOFF_DATE.date().isoformat(),
        "summary": {
            "total":       total,
            "kept":        keep_count,
            "dead":        dead_count,
            "stale":       stale_count,
            "low_quality": low_quality_count,
        },
        "feeds": [asdict(r) for r in results],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report written to:    {report_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Prune dead/stale/low-quality feeds from an OPML file."
    )
    p.add_argument("--input",     default="tiny.opml",        help="Input OPML file")
    p.add_argument("--output",    default="tiny_pruned.opml", help="Output OPML file")
    p.add_argument("--report",    default="prune_report.json", help="JSON audit report")
    p.add_argument("--model",     default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                   help="LLM model name (default: gpt-4o-mini)")
    p.add_argument("--base-url",  default="",
                   help="OpenAI-compatible API base URL (overrides OPENAI_BASE_URL)")
    p.add_argument("--api-key",   default="",
                   help="API key (overrides OPENAI_API_KEY)")
    p.add_argument("--threshold", type=int, default=5,
                   help="Minimum quality score to keep a feed (0-10, default: 5)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args))
