"""TEMPORARY (removed before merge): diagnose recall misses on captured HTML."""

import gzip
import json
import re
import statistics
from collections import Counter

from agentcrawl import parsing
from agentcrawl.challenge import html_to_plain_text
from agentcrawl.config import CrawlConfig
from benchmarks.web.report import classify, sentences


def load(path):
    return {r["id"]: r for r in map(json.loads, gzip.open(path, "rt"))}


cap = load("cap/agentcrawl.jsonl.gz")
refs = {t: load(f"ref/{t}.jsonl.gz") for t in ("crawl4ai", "firecrawl", "tavily")}
kinds = {p["id"]: p["kind"] for p in json.load(open("ref/sample.json"))["pages"]}
cfg = CrawlConfig()


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[#>|*_`]", " ", text)).strip().lower()


causes = Counter()
examples = []
from benchmarks.compare import _plain as parsing_plain  # noqa: E402
rows = []
for pid, r in cap.items():
    per = [
        sentences(ref[pid]["markdown"])
        for ref in refs.values()
        if pid in ref and classify(ref[pid]) == "content"
    ]
    if len(per) < 2:
        continue
    counts = Counter(s for found in per for s in found)
    cons = {s for s, n in counts.items() if n >= 2}
    if len(cons) < 3:
        continue
    html = r.get("html") or ""
    if r.get("error") or not html:
        causes["failed:" + str(r.get("error_type"))] += len(cons)
        continue
    md = parsing.html_to_markdown(html, cfg, base_url=r["url"])
    got = sentences(md)
    missed = cons - got
    whole = norm(html_to_plain_text(html))
    # Markdown of the whole page, nothing removed.
    everything = sentences(parsing.html_to_markdown(html, cfg, only_main_content=False))
    page_causes = Counter()
examples = []
from benchmarks.compare import _plain as parsing_plain  # noqa: E402
    plain_md = norm(parsing_plain(md))
    for sentence in missed:
        probe = sentence[:40]
        if sentence in everything:
            page_causes["dropped_by_extraction"] += 1
        elif probe in whole:
            page_causes["in_html_but_split_differently"] += 1
            if len(examples) < 40 and kinds[pid] in ("homepage", "inner"):
                at = plain_md.find(probe[:25])
                examples.append((pid, sentence[:160], plain_md[max(0, at - 60) : at + 200] if at >= 0 else "<not in md>"))
        else:
            page_causes["not_in_html (rendered by JS?)"] += 1
    causes.update(page_causes)
    causes["found"] += len(cons & got)
    rows.append(
        (
            len(cons & got) / len(cons),
            pid,
            kinds[pid],
            r.get("fetcher"),
            len(cons),
            dict(page_causes),
        )
    )
total = sum(causes.values())
print("sentence outcomes:", {k: f"{v} ({v / total:.0%})" for k, v in causes.most_common()})
rows.sort()
print("recall mean", statistics.mean(x[0] for x in rows))
for pid, sentence, around in examples:
    print("--", pid, "\n   REF:", sentence, "\n   OUR:", around.replace("\n", " / "))
for row in rows[:5]:
    print(f"{row[0]:.2f} {row[1][:40]:40s} {row[2]:8s} {row[3]} n={row[4]} {row[5]}")
