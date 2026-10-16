"""Add pages to the neutral corpus by drawing them, not picking them.

Input is a sample from ``benchmarks.web.sample`` (Tranco domains, inner pages
from sitemaps or Common Crawl, plus its hard block). Pages are kept in draw
order when they pass mechanical filters only: inner or hard-block page (no
homepage, no PDF), plain-HTTP 200 HTML without a bot challenge, robots.txt
allows it, at least ``MIN_TEXT_CHARS`` of visible text counted from the raw
HTML, at most ``MAX_PER_DOMAIN`` per domain, not already in the manifest. No
extractor runs here, so no tool's output decides which pages are scored.

New pages get empty ``expected``/``excluded``: their signals are written
afterwards from the snapshot alone, before any tool is scored on them.

Usage::

    python -m benchmarks.draw_neutral --sample draw_sample.json --want 24
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

from agentcrawl.challenge import detect_challenge

from .snapshot import DEFAULT_MANIFEST, USER_AGENT, fetch
from .web.sample import is_candidate

MIN_TEXT_CHARS = 1_500
MAX_PER_DOMAIN = 3
# Per page kind, so the hard block (news, product, forum, government) is not crowded out
# by the inner pages the sample lists first.
MAX_PER_KIND = 6


class _VisibleText(HTMLParser):
    _HIDDEN = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._HIDDEN:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._HIDDEN and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible_text(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def _robots_allow(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    robots = urllib.robotparser.RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        robots.read()
    except Exception:
        return True  # no readable robots.txt: nothing forbids it
    return robots.can_fetch(USER_AGENT, url)


def why_not(page: dict, known_urls: set[str], per_domain: dict[str, int]) -> str | None:
    """None when ``page`` qualifies, else the filter it failed."""
    url = page["url"]
    if page.get("kind") in ("homepage", "pdf") or urllib.parse.urlsplit(url).path.lower().endswith(
        ".pdf"
    ):
        return "homepage or pdf"
    if url in known_urls:
        return "already in corpus"
    domain = urllib.parse.urlsplit(url).hostname or ""
    if not is_candidate(domain.removeprefix("www.")):
        return "adult or not a site"
    if per_domain.get(domain, 0) >= MAX_PER_DOMAIN:
        return "domain full"
    if not _robots_allow(url):
        return "robots.txt"
    try:
        body, _final_url, status = fetch(url)
    except Exception as exc:
        return f"fetch: {type(exc).__name__}"
    html = body.decode("utf-8", errors="replace")
    if status != 200 or "<html" not in html[:4096].lower():
        return f"status {status} or not html"
    text = visible_text(html)
    if detect_challenge(html, text).reason:
        return "challenge"
    if len(text) < MIN_TEXT_CHARS:
        return "thin"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sample", required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--want", type=int, default=24)
    args = parser.parse_args(argv)

    sample = json.loads(Path(args.sample).read_text("utf-8"))
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    known = {page["url"] for page in manifest["pages"]}
    per_domain: dict[str, int] = {}
    reasons: dict[str, int] = {}
    added = []
    for page in sample["pages"]:
        if len(added) >= args.want:
            break
        kind = page.get("kind", "inner")
        if sum(1 for entry in added if entry["category"] == kind) >= MAX_PER_KIND:
            reasons["kind full"] = reasons.get("kind full", 0) + 1
            continue
        reason = why_not(page, known, per_domain)
        if reason:
            reasons[reason.split(":")[0]] = reasons.get(reason.split(":")[0], 0) + 1
            continue
        domain = urllib.parse.urlsplit(page["url"]).hostname or ""
        per_domain[domain] = per_domain.get(domain, 0) + 1
        added.append(
            {
                "id": f"drawn-{len(added) + 1:02d}",
                "category": page.get("kind", "inner"),
                "url": page["url"],
                "source": page.get("source"),
                "expected": [],
                "excluded": [],
            }
        )
        print(f"kept {page['url']}", file=sys.stderr)
    manifest["pages"].extend(added)
    manifest.setdefault("draws", []).append(
        {
            "seed": sample.get("seed"),
            "sample_size": len(sample["pages"]),
            "kept": len(added),
            "rejected": reasons,
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({"kept": len(added), "rejected": reasons}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
