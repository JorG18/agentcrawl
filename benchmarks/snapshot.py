"""Freeze the neutral corpus: fetch each manifest URL once and record its hash.

Every tool is then scored on the same bytes (``compare --corpus``), so a
result is reproducible even when the live page changes. Snapshots are
third-party content and stay out of git; ``snapshots/index.json`` records the
URL, final URL, status, size, SHA-256 and fetch time of each one, and those
hashes travel with any published numbers.

Usage::

    python -m benchmarks.snapshot                                  # default manifest
    python -m benchmarks.snapshot benchmarks/corpus/neutral.json --only pep-8
    python -m benchmarks.snapshot --refresh                        # re-fetch existing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

DEFAULT_MANIFEST = Path(__file__).resolve().parent / "corpus" / "neutral.json"
USER_AGENT = "Mozilla/5.0 (compatible; AgentCrawl-benchmark/1; snapshot)"
MAX_BYTES = 10 * 1024 * 1024

Fetcher = Callable[[str], tuple[bytes, str, int]]


def fetch(url: str) -> tuple[bytes, str, int]:
    """Plain GET, the same for every page: ``(body, final_url, status)``."""
    request = urllib.request.Request(url, headers={"user-agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError(f"page larger than {MAX_BYTES} bytes")
        return body, response.geturl(), getattr(response, "status", 200)


def snapshot(
    manifest_path: Path,
    *,
    only: set[str] | None = None,
    refresh: bool = False,
    fetcher: Fetcher = fetch,
) -> dict[str, dict[str, object]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    directory = manifest_path.parent / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / "index.json"
    index: dict[str, dict[str, object]] = (
        json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    )
    for entry in manifest["pages"]:
        page_id = entry["id"]
        target = directory / f"{page_id}.html"
        if (only and page_id not in only) or (target.exists() and not refresh):
            continue
        try:
            body, final_url, status = fetcher(entry["url"])
        except Exception as exc:
            index[page_id] = {"url": entry["url"], "error": f"{type(exc).__name__}: {exc}"}
            continue
        target.write_bytes(body)
        index[page_id] = {
            "url": entry["url"],
            "final_url": final_url,
            "status": status,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", nargs="?", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--only", action="append", help="Page id to fetch (repeatable).")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch existing snapshots.")
    args = parser.parse_args(argv)
    index = snapshot(Path(args.manifest), only=set(args.only or ()), refresh=args.refresh)
    failed = {page: item["error"] for page, item in index.items() if "error" in item}
    print(json.dumps({"snapshots": len(index) - len(failed), "failed": failed}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
