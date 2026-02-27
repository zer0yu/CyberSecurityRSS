# CyberSecurityRSS

[![Stars](https://img.shields.io/github/stars/zer0yu/CyberSecurityRSS?style=flat-square)](https://github.com/zer0yu/CyberSecurityRSS/stargazers)
[![Forks](https://img.shields.io/github/forks/zer0yu/CyberSecurityRSS?style=flat-square)](https://github.com/zer0yu/CyberSecurityRSS/network/members)
[![Last Commit](https://img.shields.io/github/last-commit/zer0yu/CyberSecurityRSS?style=flat-square)](https://github.com/zer0yu/CyberSecurityRSS/commits/master)
[![OPML Sync](https://img.shields.io/github/actions/workflow/status/zer0yu/CyberSecurityRSS/opml-sync.yml?branch=master&style=flat-square&label=opml-sync)](https://github.com/zer0yu/CyberSecurityRSS/actions/workflows/opml-sync.yml)

A curated cybersecurity RSS/Atom OPML collection for building a high-signal daily intelligence workflow.

[中文文档](README.zh-CN.md)

## Highlights

- Two profiles for different reading budgets:
  - `tiny.opml`: focused daily reading (currently ~422 feeds / 9 categories)
  - `CyberSecurityRSS.opml`: broad coverage (currently ~735 feeds / 12 categories)
- Practical security coverage across vulnerability research, red team, reverse engineering, web security, threat news, and more.
- Automation-first maintenance: dead feed cleanup, deduplication, and tiny -> full sync.
- Ready for both manual reading and AI-assisted daily digest pipelines.

## Quick Start

1. Choose your feed set:
   - Low-noise daily reading: `tiny.opml`
   - Full coverage: `CyberSecurityRSS.opml`
2. Import the OPML file into your favorite reader.
3. Start tracking updates as your personal security knowledge base.

### Reader Examples

- Reeder (macOS / iOS)
- Feedly (Web / iOS / Android)
- [yarr](https://github.com/nkanaev/yarr) (self-hosted, cross-platform)
- Feeder / Zr / Anyant / Leaf

## Files

- `tiny.opml`: streamlined high-signal security feed set.
- `CyberSecurityRSS.opml`: full feed set with wider topic coverage.
- `others.md`: hand-picked useful sites, including some without RSS.

## AI Daily Digest (OpenClaw / Claude Code)

You can combine this repository with the Skills in [zer0yu/sec-daily-digest](https://github.com/zer0yu/sec-daily-digest) to generate an automated **curated daily cybersecurity digest**.

Recommended workflow:

1. Install sec-daily-digest Skills in OpenClaw or Claude Code (follow that repository's guide).
2. Use `tiny.opml` as default input (or `CyberSecurityRSS.opml` for broader coverage).
3. Run once per day (for example, every morning) with a 24-hour time window.
4. Output a structured report, such as:
   - Top picks
   - Vulnerabilities & exploitation
   - Threat intelligence & incidents
   - Research / tooling
   - Actionable takeaways

Prompt example:

```text
Use CyberSecurityRSS feeds to generate a curated daily security digest for the last 24 hours.
Keep only high-signal items, group by topic, and include title, why-it-matters, and source URL.
```

## Repository Automation

This repository uses GitHub Actions to keep OPML files healthy and synced:

1. `pull_request -> master`: runs in `check` mode and fails when OPML drift is detected.
2. `push -> master`: runs in `apply` mode:
   - Validate RSS/Atom feed URLs in `tiny.opml` and `CyberSecurityRSS.opml`.
   - Remove dead feed entries and deduplicate by `xmlUrl`.
   - Sync valid feeds from `tiny.opml` into `CyberSecurityRSS.opml`.
   - Fallback missing categories to `Misc`.
   - Keep `.github/opml-health-state.json` and only remove feeds after consecutive hard failures.
   - Auto-commit OPML changes with `[skip ci]`.

## Local Validation

```bash
python3 -m unittest discover -s tests -v

python3 scripts/opml_sync.py \
  --mode check \
  --tiny tiny.opml \
  --full CyberSecurityRSS.opml \
  --fallback-category Misc \
  --timeout 10 \
  --retries 3
```

## Add New RSS Into tiny.opml (Interactive)

Use this helper script when you discover a new feed URL:

```bash
uv run python scripts/add_feed_to_tiny.py
```

Default behavior:

- Runs `git pull --ff-only` first to reduce local/remote drift before editing.
- Prints current top-level categories in `tiny.opml`.
- Lets you select a category by number or type a new name (auto-create).
- Fetches feed metadata (title/site link) from your RSS URL, then appends it.

Common options:

```bash
# Non-interactive
uv run python scripts/add_feed_to_tiny.py \
  --url "https://example.com/feed.xml" \
  --category "ThreatIntel"

# Skip startup git pull
uv run python scripts/add_feed_to_tiny.py --no-git-pull
```

## OPML/XML Compatibility

Some readers only accept a specific extension. You can safely rename files:

```bash
cp CyberSecurityRSS.opml CyberSecurityRSS.xml
cp tiny.opml tiny.xml
```

## Contributing

Contributions are welcome.

- Submit an Issue or PR for high-quality feeds.
- Include feed URL, category suggestion, and why it is valuable.
- Run local checks before opening the PR.

## Sponsor

[![Powered by DartNode](https://dartnode.com/branding/DN-Open-Source-sm.png)](https://dartnode.com "Powered by DartNode - Free VPS for Open Source")
