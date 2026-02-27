import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.opml_sync import FeedCheckResult, run_sync


def write_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def parse_category_urls(path: Path):
    tree = ET.parse(path)
    body = tree.getroot().find("body")
    if body is None:
        raise AssertionError("Missing body")
    result = {}
    for category in list(body):
        if category.tag != "outline":
            continue
        name = category.attrib.get("title") or category.attrib.get("text") or ""
        urls = []
        for feed in list(category):
            if feed.tag == "outline" and feed.attrib.get("type") == "rss" and feed.attrib.get("xmlUrl"):
                urls.append(feed.attrib["xmlUrl"])
        result[name] = urls
    return result


class OpmlSyncTests(unittest.TestCase):
    def test_apply_cleans_dedupes_and_syncs_with_misc_fallback(self):
        tiny_content = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head>
<title>Tiny</title>
</head>
<body>
<outline title="Dev" text="Dev">
<outline type="rss" text="A" title="A" htmlUrl="https://a.example" xmlUrl="https://feed-a.example/rss" />
<outline type="rss" text="A-dup" title="A-dup" htmlUrl="https://a.example" xmlUrl="https://feed-a.example/rss" />
<outline type="rss" text="DeadTiny" title="DeadTiny" htmlUrl="https://dead-tiny.example" xmlUrl="https://dead-tiny.example/rss" />
<outline type="rss" text="DevNew" title="DevNew" htmlUrl="https://dev-new.example" xmlUrl="https://dev-new.example/rss" />
</outline>
<outline title="UnknownCategory" text="UnknownCategory">
<outline type="rss" text="FallbackNew" title="FallbackNew" htmlUrl="https://fallback.example" xmlUrl="https://fallback.example/rss" />
</outline>
</body>
</opml>
"""

        full_content = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head>
<title>CyberSecurityRSS</title>
</head>
<body>
<outline title="Dev" text="Dev">
<outline type="rss" text="A" title="A" htmlUrl="https://a.example" xmlUrl="https://feed-a.example/rss" />
<outline type="rss" text="DeadFull" title="DeadFull" htmlUrl="https://dead-full.example" xmlUrl="https://dead-full.example/rss" />
</outline>
<outline title="Other" text="Other">
<outline type="rss" text="Dup" title="Dup" htmlUrl="https://dup.example" xmlUrl="https://dup.example/rss" />
</outline>
<outline title="Another" text="Another">
<outline type="rss" text="Dup Again" title="Dup Again" htmlUrl="https://dup.example" xmlUrl="https://dup.example/rss" />
</outline>
</body>
</opml>
"""

        def checker(url: str) -> FeedCheckResult:
            if url in {
                "https://feed-a.example/rss",
                "https://dev-new.example/rss",
                "https://fallback.example/rss",
                "https://dup.example/rss",
            }:
                return FeedCheckResult(True, "alive", "ok")
            return FeedCheckResult(False, "hard_fail", "http_404", 404)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tiny = tmp_path / "tiny.opml"
            full = tmp_path / "CyberSecurityRSS.opml"
            state_file = tmp_path / "state.json"
            write_file(tiny, tiny_content)
            write_file(full, full_content)

            stats, changed = run_sync(
                tiny_path=tiny,
                full_path=full,
                mode="apply",
                fallback_category="Misc",
                timeout=10,
                retries=3,
                workers=4,
                state_file=state_file,
                delete_threshold=1,
                max_probe_bytes=128 * 1024,
                checker=checker,
            )

            self.assertTrue(changed)
            self.assertEqual(stats.dead_removed_tiny, 1)
            self.assertEqual(stats.dead_removed_full, 1)
            self.assertEqual(stats.duplicates_removed_tiny, 1)
            self.assertEqual(stats.duplicates_removed_full, 1)
            self.assertEqual(stats.merged_added_full, 2)
            self.assertTrue(state_file.exists())

            tiny_urls = parse_category_urls(tiny)
            self.assertEqual(
                tiny_urls["Dev"],
                [
                    "https://feed-a.example/rss",
                    "https://dev-new.example/rss",
                ],
            )
            self.assertEqual(tiny_urls["UnknownCategory"], ["https://fallback.example/rss"])

            full_urls = parse_category_urls(full)
            self.assertIn("Misc", full_urls)
            self.assertIn("https://dev-new.example/rss", full_urls["Dev"])
            self.assertIn("https://fallback.example/rss", full_urls["Misc"])
            self.assertEqual(full_urls["Another"], [])

    def test_check_mode_detects_change_but_does_not_write(self):
        tiny_content = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head><title>Tiny</title></head>
<body>
<outline title="Dev" text="Dev">
<outline type="rss" text="Dead" title="Dead" htmlUrl="https://dead.example" xmlUrl="https://dead.example/rss" />
</outline>
</body>
</opml>
"""
        full_content = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head><title>CyberSecurityRSS</title></head>
<body>
<outline title="Dev" text="Dev">
<outline type="rss" text="Alive" title="Alive" htmlUrl="https://alive.example" xmlUrl="https://alive.example/rss" />
</outline>
</body>
</opml>
"""

        def checker(url: str) -> FeedCheckResult:
            if url == "https://alive.example/rss":
                return FeedCheckResult(True, "alive", "ok")
            return FeedCheckResult(False, "hard_fail", "http_404", 404)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tiny = tmp_path / "tiny.opml"
            full = tmp_path / "CyberSecurityRSS.opml"
            state_file = tmp_path / "state.json"
            write_file(tiny, tiny_content)
            write_file(full, full_content)
            tiny_before = tiny.read_bytes()
            full_before = full.read_bytes()

            stats, changed = run_sync(
                tiny_path=tiny,
                full_path=full,
                mode="check",
                fallback_category="Misc",
                timeout=10,
                retries=3,
                workers=4,
                state_file=state_file,
                delete_threshold=1,
                max_probe_bytes=128 * 1024,
                checker=checker,
            )

            self.assertTrue(changed)
            self.assertEqual(stats.dead_removed_tiny, 1)
            self.assertEqual(tiny.read_bytes(), tiny_before)
            self.assertEqual(full.read_bytes(), full_before)
            self.assertFalse(state_file.exists())

    def test_no_immediate_delete_for_hard_fail_when_threshold_is_two(self):
        tiny_content = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head><title>Tiny</title></head>
<body>
<outline title="Dev" text="Dev">
<outline type="rss" text="Dead" title="Dead" htmlUrl="https://dead.example" xmlUrl="https://dead.example/rss" />
</outline>
</body>
</opml>
"""
        full_content = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head><title>CyberSecurityRSS</title></head>
<body>
<outline title="Dev" text="Dev">
</outline>
</body>
</opml>
"""

        def checker(_url: str) -> FeedCheckResult:
            return FeedCheckResult(False, "hard_fail", "http_404", 404)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tiny = tmp_path / "tiny.opml"
            full = tmp_path / "CyberSecurityRSS.opml"
            state_file = tmp_path / "state.json"
            write_file(tiny, tiny_content)
            write_file(full, full_content)

            first_stats, _ = run_sync(
                tiny_path=tiny,
                full_path=full,
                mode="apply",
                fallback_category="Misc",
                timeout=10,
                retries=3,
                workers=4,
                state_file=state_file,
                delete_threshold=2,
                max_probe_bytes=128 * 1024,
                checker=checker,
            )
            self.assertEqual(first_stats.dead_removed_tiny, 0)
            self.assertEqual(first_stats.retained_failed_tiny, 1)
            self.assertIn("https://dead.example/rss", tiny.read_text(encoding="utf-8"))

            second_stats, _ = run_sync(
                tiny_path=tiny,
                full_path=full,
                mode="apply",
                fallback_category="Misc",
                timeout=10,
                retries=3,
                workers=4,
                state_file=state_file,
                delete_threshold=2,
                max_probe_bytes=128 * 1024,
                checker=checker,
            )
            self.assertEqual(second_stats.dead_removed_tiny, 1)
            self.assertNotIn("https://dead.example/rss", tiny.read_text(encoding="utf-8"))

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn("urls", state)


if __name__ == "__main__":
    unittest.main()
