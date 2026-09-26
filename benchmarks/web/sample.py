"""Draw a sample of real web pages that nobody here picked.

Domains come from the Tranco list (a research ranking of popular sites that
is hard to game), stratified by rank so the long tail is represented, not
only the top 100. Half of the sampled domains contribute their homepage; the
other half contribute an inner page that Common Crawl has seen return HTML,
picked at random. The seed, the Tranco list and the Common Crawl index used
are recorded in the output, so the same sample can be drawn again.

Usage::

    python -m benchmarks.web.sample --size 500 --seed 2026 --out sample.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"
USER_AGENT = "AgentCrawl-benchmark/1 (sample builder)"

# (first rank, last rank, share of the sample)
STRATA = ((1, 1_000, 0.4), (1_001, 10_000, 0.4), (10_001, 100_000, 0.2))

# Ranked domains that are infrastructure, not websites (CDNs, APIs, trackers,
# DNS), and adult sites. Matching is on the registered domain's text.
_NOT_A_SITE = (
    "googleapis",
    "gstatic",
    "googlevideo",
    "googleusercontent",
    "doubleclick",
    "googlesyndication",
    "googletagmanager",
    "google-analytics",
    "akamai",
    "cloudfront",
    "amazonaws",
    "azureedge",
    "fastly",
    "cdn",
    "edgekey",
    "edgesuite",
    "trafficmanager",
    "msftconnecttest",
    "digicert",
    "sentry",
    "adnxs",
    "rubiconproject",
    "criteo",
    "tiktokv",
    "fbcdn",
    "whatsapp.net",
    "icloud-content",
    "apple-dns",
    "root-servers",
    "in-addr",
    "ntp",
    "dns",
)
_ADULT = ("porn", "xxx", "xvideos", "xhamster", "sex", "hentai", "onlyfans", "cam4", "chaturbate")


def _get(url: str, *, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"user-agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def load_tranco(limit: int = 100_000) -> list[tuple[int, str]]:
    archive = zipfile.ZipFile(io.BytesIO(_get(TRANCO_URL, timeout=120)))
    name = archive.namelist()[0]
    rows: list[tuple[int, str]] = []
    with archive.open(name) as handle:
        for rank, domain in csv.reader(io.TextIOWrapper(handle, "utf-8")):
            if int(rank) > limit:
                break
            rows.append((int(rank), domain.strip().lower()))
    return rows


def is_candidate(domain: str) -> bool:
    return not any(token in domain for token in _NOT_A_SITE + _ADULT)


def latest_cc_index() -> str:
    collections = json.loads(_get(CC_COLLINFO))
    return collections[0]["cdx-api"]


def inner_page(domain: str, index_api: str, rng: random.Random) -> str | None:
    """A random HTML page of ``domain`` that Common Crawl fetched with a 200."""
    query = urllib.parse.urlencode(
        {
            "url": domain,
            "matchType": "domain",
            "output": "json",
            "limit": "200",
            "filter": ["status:200", "mime:text/html"],
        },
        doseq=True,
    )
    for attempt in range(3):
        try:
            body = _get(f"{index_api}?{query}", timeout=60).decode("utf-8", "replace")
            break
        except Exception:
            time.sleep(2 + attempt * 3)
    else:
        return None
    candidates = []
    for line in body.splitlines():
        try:
            url = json.loads(line)["url"]
        except (ValueError, KeyError):
            continue
        parsed = urllib.parse.urlsplit(url)
        depth = len([part for part in parsed.path.split("/") if part])
        if parsed.scheme == "https" and not parsed.query and 1 <= depth <= 5:
            candidates.append(url)
    candidates = sorted(set(candidates))
    return rng.choice(candidates) if candidates else None


def build_sample(size: int, seed: int, *, workers: int = 6) -> dict[str, object]:
    rng = random.Random(seed)
    ranked = [row for row in load_tranco() if is_candidate(row[1])]
    chosen: list[tuple[int, str, str]] = []
    for first, last, share in STRATA:
        pool = [row for row in ranked if first <= row[0] <= last]
        count = round(size * share)
        label = f"{first}-{last}"
        chosen += [
            (rank, domain, label) for rank, domain in rng.sample(pool, min(count, len(pool)))
        ]
    chosen.sort()
    index_api = latest_cc_index()

    def page(item: tuple[int, tuple[int, str, str]]) -> dict[str, object]:
        position, (rank, domain, stratum) = item
        entry: dict[str, object] = {
            "id": f"{position:04d}-{domain}",
            "domain": domain,
            "rank": rank,
            "stratum": stratum,
            "tld": domain.rsplit(".", 1)[-1],
        }
        if position % 2:
            url = inner_page(domain, index_api, random.Random(f"{seed}-{domain}"))
            if url:
                return {**entry, "url": url, "kind": "inner"}
        return {**entry, "url": f"https://{domain}/", "kind": "homepage"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pages = list(pool.map(page, enumerate(chosen)))
    return {
        "seed": seed,
        "size": len(pages),
        "tranco": TRANCO_URL,
        "common_crawl_index": index_api,
        "drawn_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": pages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--out", default="sample.json")
    args = parser.parse_args(argv)
    sample = build_sample(args.size, args.seed)
    Path(args.out).write_text(json.dumps(sample, indent=1) + "\n", encoding="utf-8")
    kinds = [page["kind"] for page in sample["pages"]]
    print(
        f"{len(kinds)} pages: {kinds.count('homepage')} homepages, {kinds.count('inner')} inner",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
