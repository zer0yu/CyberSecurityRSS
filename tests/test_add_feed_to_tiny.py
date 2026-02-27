import unittest
import xml.etree.ElementTree as ET

from scripts.add_feed_to_tiny import (
    FeedMetadata,
    add_feed_to_tree,
    parse_feed_metadata,
)


def parse_opml(content: str) -> ET.ElementTree:
    return ET.ElementTree(ET.fromstring(content))


def rss_urls_in_category(tree: ET.ElementTree, category_name: str):
    body = tree.getroot().find("body")
    if body is None:
        raise AssertionError("missing body")
    for child in list(body):
        if child.tag != "outline":
            continue
        name = (child.attrib.get("title") or child.attrib.get("text") or "").strip()
        if name != category_name:
            continue
        urls = []
        for rss in list(child):
            if rss.tag == "outline" and rss.attrib.get("type") == "rss" and rss.attrib.get("xmlUrl"):
                urls.append(rss.attrib["xmlUrl"])
        return urls
    return []


class AddFeedToTinyTests(unittest.TestCase):
    def test_parse_rss_metadata(self):
        xml_bytes = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss version="2.0">
  <channel>
    <title>Example RSS</title>
    <link>https://example.com</link>
  </channel>
</rss>
"""
        meta = parse_feed_metadata(xml_bytes, "https://example.com/feed.xml")
        self.assertEqual(meta.title, "Example RSS")
        self.assertEqual(meta.html_url, "https://example.com")
        self.assertEqual(meta.xml_url, "https://example.com/feed.xml")

    def test_parse_atom_metadata(self):
        xml_bytes = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <link rel="alternate" href="https://atom.example.com" />
</feed>
"""
        meta = parse_feed_metadata(xml_bytes, "https://atom.example.com/feed")
        self.assertEqual(meta.title, "Example Atom")
        self.assertEqual(meta.html_url, "https://atom.example.com")
        self.assertEqual(meta.xml_url, "https://atom.example.com/feed")

    def test_add_feed_creates_missing_category(self):
        tree = parse_opml(
            """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Tiny</title></head>
  <body>
    <outline title="Dev" text="Dev" />
  </body>
</opml>
"""
        )
        added, target = add_feed_to_tree(
            tree=tree,
            category_name="ThreatIntel",
            metadata=FeedMetadata(
                title="Threat News",
                html_url="https://ti.example.com",
                xml_url="https://ti.example.com/feed.xml",
            ),
        )
        self.assertTrue(added)
        self.assertEqual(target, "ThreatIntel")
        self.assertEqual(
            rss_urls_in_category(tree, "ThreatIntel"),
            ["https://ti.example.com/feed.xml"],
        )

    def test_add_feed_skips_duplicate_xml_url(self):
        tree = parse_opml(
            """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Tiny</title></head>
  <body>
    <outline title="Dev" text="Dev">
      <outline type="rss" title="Exists" text="Exists"
               htmlUrl="https://x.example.com"
               xmlUrl="https://x.example.com/feed.xml" />
    </outline>
  </body>
</opml>
"""
        )
        added, target = add_feed_to_tree(
            tree=tree,
            category_name="Dev",
            metadata=FeedMetadata(
                title="Exists Again",
                html_url="https://x.example.com",
                xml_url="https://x.example.com/feed.xml",
            ),
        )
        self.assertFalse(added)
        self.assertEqual(target, "Dev")
        self.assertEqual(
            rss_urls_in_category(tree, "Dev"),
            ["https://x.example.com/feed.xml"],
        )


if __name__ == "__main__":
    unittest.main()
