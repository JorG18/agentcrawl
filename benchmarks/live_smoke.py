"""Scrape real public pages and check that AgentCrawl gets usable content.

The offline fixtures prove the parser; this proves the product. Each page in
``corpus/live_smoke.json`` says what a correct result looks like:

- ``expect: "ok"``: the scrape succeeds, the Markdown holds at least
  ``min_chars`` characters and every string in ``must_contain``.
- ``expect: "challenge"``: the site answers non-browser clients with a
  challenge, so the honest result is ``error_type`` ``client_challenge`` or
  ``blocked`` with no content. Returning challenge text as content fails.

Usage::

    python -m benchmarks.live_smoke                     # all pages
    python -m benchmarks.live_smoke --only wikipedia-web-crawler
    python -m benchmarks.live_smoke --json smoke.json --max-failures 1

Exit status is 1 when more pages fail than ``--max-failures`` allows, so the
workflow goes red on a real regression but a single site having a bad day
does not block a pull request.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from agentcrawl import AgentCrawl
from agentcrawl.utils import estimate_tokens

DEFAULT_MANIFEST = Path(__file__).resolve().parent / "corpus" / "live_smoke.json"
_CHALLENGE_TYPES = {"client_challenge", "blocked"}


def check_page(entry: dict[str, Any], crawler: AgentCrawl) -> dict[str, Any]:
    started = time.monotonic()
    try:
        document = crawler.scrape(entry["url"])
    except Exception as exc:  # the smoke test reports, it never crashes
        return {
            "id": entry["id"],
            "passed": False,
            "reason": f"exception {type(exc).__name__}: {exc}",
            "seconds": round(time.monotonic() - started, 2),
        }
    seconds = round(time.monotonic() - started, 2)
    markdown = document.markdown or ""
    metadata = document.metadata or {}
    error_type = metadata.get("error_type")
    result: dict[str, Any] = {
        "id": entry["id"],
        "url": entry["url"],
        "expect": entry.get("expect", "ok"),
        "error_type": error_type,
        "fetcher": metadata.get("fetcher"),
        "chars": len(markdown),
        "tokens": estimate_tokens(markdown),
        "seconds": seconds,
    }
    reasons: list[str] = []
    if result["expect"] == "challenge":
        if error_type not in _CHALLENGE_TYPES:
            reasons.append(f"expected a challenge/blocked error, got {error_type or 'content'}")
    else:
        if error_type:
            reasons.append(f"{error_type}: {metadata.get('error_message', '')[:160]}")
        if len(markdown) < int(entry.get("min_chars", 500)):
            reasons.append(f"only {len(markdown)} chars")
        lowered = markdown.casefold()
        missing = [text for text in entry.get("must_contain", []) if text.casefold() not in lowered]
        if missing:
            reasons.append(f"missing {missing}")
    result["passed"] = not reasons
    result["reason"] = "; ".join(reasons)
    return result


def render_markdown(results: list[dict[str, Any]]) -> str:
    passed = sum(1 for item in results if item["passed"])
    lines = [
        f"## Live smoke: {passed}/{len(results)} pages passed",
        "",
        "| Page | Result | Fetcher | Chars | ~Tokens | Seconds | Notes |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in results:
        mark = "pass" if item["passed"] else "FAIL"
        lines.append(
            f"| {item['id']} | {mark} | {item.get('fetcher') or ''} | {item.get('chars', 0)} "
            f"| {item.get('tokens', 0)} | {item.get('seconds', 0)} "
            f"| {(item.get('reason') or item.get('error_type') or '').replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--only", action="append", default=[], help="page id (repeatable)")
    parser.add_argument("--json", type=Path, help="write the full results here")
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true", help="HTTP only, no fallback")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pages = [page for page in manifest["pages"] if not args.only or page["id"] in args.only]
    crawler = AgentCrawl({"browser_fallback": not args.no_browser})
    results = [check_page(page, crawler) for page in pages]

    report = render_markdown(results)
    sys.stdout.write(report)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    failures = sum(1 for item in results if not item["passed"])
    return 1 if failures > args.max_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
