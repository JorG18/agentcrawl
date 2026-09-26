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
import re
import sys
import time
import urllib.error
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
_ADULT = (
    "porn",
    "xxx",
    "xvideos",
    "xhamster",
    "sex",
    "hentai",
    "onlyfans",
    "cam4",
    "chaturbate",
    "javhd",
    "javmost",
    "javlibrary",
    "missav",
    "stripchat",
    "spankbang",
)


# The hard block: page types agents need and scrapers often get wrong. The
# categories are fixed here, before any tool has run; the pages inside each
# one are drawn at random like the rest of the sample.
_GOV_RE = re.compile(r"(?:^|\.)(?:gov|gob|gouv|go|govt|gv)\.[a-z]{2}$|\.gov$|\.mil$")
HARD_CATEGORIES: dict[str, dict[str, object]] = {
    "product": {
        "pattern": re.compile(r"/(?:products?|p|dp|item|itm|shop|producto|produit|artikel)/", re.I)
    },
    "news": {
        "pattern": re.compile(
            r"/(?:news|article|articles|story|noticias?|nachrichten)/|/20[12]\d/", re.I
        )
    },
    "forum": {
        "pattern": re.compile(
            r"/(?:threads?|forums?|topics?|t|questions|discussions?|comments)/", re.I
        )
    },
    "government": {"domains": _GOV_RE},
    "pdf": {"mime": "application/pdf", "pattern": re.compile(r"\.pdf$", re.I)},
}


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


def inner_page(
    domain: str,
    index_api: str,
    rng: random.Random,
    *,
    mime: str = "text/html",
    pattern: re.Pattern[str] | None = None,
    deadline: float | None = None,
) -> str | None:
    """A random page of ``domain`` that Common Crawl fetched with a 200.

    ``pattern``, when given, must match the URL's path (a product page, a
    forum thread...).
    """
    query = urllib.parse.urlencode(
        {
            "url": domain,
            "matchType": "domain",
            "output": "json",
            "limit": "200",
            "filter": ["status:200", f"mime:{mime}"],
        },
        doseq=True,
    )
    # The index server sheds load with 503s; back off instead of giving up,
    # or the sample silently turns into homepages only.
    # At most ~2.5 minutes per domain: a hung index request must not stall a
    # 500-page sample for hours.
    for attempt in range(4):
        if deadline is not None and time.monotonic() > deadline:
            return None
        try:
            body = _get(f"{index_api}?{query}", timeout=30).decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # the index has no page of this domain
                return None
            time.sleep(5 * 2**attempt)
        except Exception:
            time.sleep(5 * 2**attempt)
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
        if parsed.scheme != "https" or parsed.query or not 1 <= depth <= 6:
            continue
        if pattern is None or pattern.search(parsed.path):
            candidates.append(url)
    candidates = sorted(set(candidates))
    return rng.choice(candidates) if candidates else None


def hard_block(
    count: int,
    seed: int,
    ranked: list[tuple[int, str]],
    index_api: str,
    *,
    workers: int = 4,
    deadline: float | None = None,
) -> list[dict[str, object]]:
    """``count`` pages split evenly over :data:`HARD_CATEGORIES`.

    For each category, domains are drawn at random from the ranking and asked
    for a matching page until the category is full or the draws run out.
    """
    rng = random.Random(f"{seed}-hard")
    per_category = max(1, count // len(HARD_CATEGORIES))
    used: set[str] = set()
    pages: list[dict[str, object]] = []
    for category, rule in HARD_CATEGORIES.items():
        domains = [row for row in ranked if row[1] not in used]
        if "domains" in rule:
            domains = [row for row in domains if rule["domains"].search(row[1])]  # type: ignore[union-attr]
        order = rng.sample(domains, min(len(domains), per_category * 8))
        found: list[dict[str, object]] = []

        def lookup(
            row: tuple[int, str], category: str = category
        ) -> tuple[tuple[int, str], str | None]:
            rule = HARD_CATEGORIES[category]
            url = inner_page(
                row[1],
                index_api,
                random.Random(f"{seed}-{category}-{row[1]}"),
                mime=str(rule.get("mime", "text/html")),
                pattern=rule.get("pattern"),  # type: ignore[arg-type]
                deadline=deadline,
            )
            return row, url

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(order), workers * 4):
                for (rank, domain), url in pool.map(lookup, order[start : start + workers * 4]):
                    if url and len(found) < per_category:
                        used.add(domain)
                        found.append(
                            {
                                "domain": domain,
                                "rank": rank,
                                "stratum": "hard",
                                "tld": domain.rsplit(".", 1)[-1],
                                "url": url,
                                "kind": category,
                                "block": "hard",
                            }
                        )
                if len(found) >= per_category:
                    break
                if deadline is not None and time.monotonic() > deadline:
                    break
        print(f"hard block: {category} {len(found)}/{per_category}", file=sys.stderr)
        pages += found
    return pages


# Wall-clock budget for Common Crawl lookups, per block. When the index is
# slow, the remaining random positions use homepages and the hard block ends
# short; both are counted in the sample's metadata, never hidden.
LOOKUP_BUDGET_S = 900.0


def build_sample(size: int, seed: int, *, hard: int = 0, workers: int = 4) -> dict[str, object]:
    rng = random.Random(seed)
    ranked = [row for row in load_tranco() if is_candidate(row[1])]
    chosen: list[tuple[int, str, str]] = []
    for first, last, share in STRATA:
        pool = [row for row in ranked if first <= row[0] <= last]
        count = round((size - hard) * share)
        label = f"{first}-{last}"
        chosen += [
            (rank, domain, label) for rank, domain in rng.sample(pool, min(count, len(pool)))
        ]
    chosen.sort()
    index_api = latest_cc_index()
    deadline = time.monotonic() + LOOKUP_BUDGET_S

    def page(item: tuple[int, tuple[int, str, str]]) -> dict[str, object]:
        position, (rank, domain, stratum) = item
        entry: dict[str, object] = {
            "id": f"{position:04d}-{domain}",
            "domain": domain,
            "rank": rank,
            "stratum": stratum,
            "tld": domain.rsplit(".", 1)[-1],
            "block": "random",
        }
        if position % 2:
            url = inner_page(
                domain, index_api, random.Random(f"{seed}-{domain}"), deadline=deadline
            )
            if url:
                return {**entry, "url": url, "kind": "inner"}
        return {**entry, "url": f"https://{domain}/", "kind": "homepage"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pages = list(pool.map(page, enumerate(chosen)))
    if hard:
        taken = {domain for _, domain, _ in chosen}
        extra = hard_block(
            hard,
            seed,
            [r for r in ranked if r[1] not in taken],
            index_api,
            deadline=time.monotonic() + LOOKUP_BUDGET_S,
        )
        pages += [{"id": f"{len(pages) + i:04d}-{p['domain']}", **p} for i, p in enumerate(extra)]
    return {
        "seed": seed,
        "size": len(pages),
        "tranco": TRANCO_URL,
        "common_crawl_index": index_api,
        "drawn_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inner_pages_wanted": sum(1 for position in range(len(chosen)) if position % 2),
        "hard_pages_wanted": hard,
        "pages": pages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--hard", type=int, default=0, help="Pages in the hard block.")
    parser.add_argument("--out", default="sample.json")
    args = parser.parse_args(argv)
    sample = build_sample(args.size, args.seed, hard=args.hard)
    Path(args.out).write_text(json.dumps(sample, indent=1) + "\n", encoding="utf-8")
    kinds = [page["kind"] for page in sample["pages"]]
    print(
        f"{len(kinds)} pages: {kinds.count('homepage')} homepages, {kinds.count('inner')} inner, "
        f"{len(kinds) - kinds.count('homepage') - kinds.count('inner')} hard",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
