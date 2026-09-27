"""TEMPORARY (removed before merge): score extraction variants on captured HTML."""
import gzip, json, statistics, sys
from collections import Counter
from benchmarks.web.report import classify, sentences
from agentcrawl import parsing
from agentcrawl.config import CrawlConfig
from agentcrawl.utils import estimate_tokens

def load(p): return {r["id"]: r for r in map(json.loads, gzip.open(p, "rt"))}
cap = load("cap/agentcrawl.jsonl.gz")
refs = {t: load(f"ref/{t}.jsonl.gz") for t in ("crawl4ai", "firecrawl", "tavily")}
kinds = {p["id"]: p["kind"] for p in json.load(open("ref/sample.json"))["pages"]}
cfg = CrawlConfig()
ref_sets = {}
for pid in cap:
    per = [sentences(r[pid]["markdown"]) for r in refs.values() if pid in r and classify(r[pid]) == "content"]
    if len(per) < 2: continue
    c = Counter(s for f in per for s in f)
    cons = {s for s, n in c.items() if n >= 2}
    if len(cons) >= 3: ref_sets[pid] = cons

def score(label):
    rec, toks, by_kind, content = [], [], {}, 0
    for pid, r in cap.items():
        html = r.get("html")
        md = parsing.html_to_markdown(html, cfg, base_url=r["url"]) if html and not r.get("error") else (r.get("markdown") or "")
        if classify({"markdown": md, "error": r.get("error")}) == "content": content += 1
        if pid not in ref_sets: continue
        got = sentences(md) if classify({"markdown": md}) == "content" else set()
        v = len(got & ref_sets[pid]) / len(ref_sets[pid])
        rec.append(v); toks.append(estimate_tokens(md)); by_kind.setdefault(kinds[pid], []).append(v)
    print(f"{label:28s} content={content} recall={statistics.mean(rec):.3f} (n={len(rec)}) tokens med={statistics.median(toks):.0f} mean={statistics.mean(toks):.0f} "
          + " ".join(f"{k}={statistics.mean(v):.3f}/{len(v)}" for k, v in sorted(by_kind.items())), flush=True)

parsing._HOMEPAGE_SHARE = 0
score("current (no homepage mode)")
for guard in ("none", "tag", "h1"):
    for share in (0.4, 0.6, 0.8):
        parsing._HOMEPAGE_SHARE, parsing._ARTICLE_GUARD = share, guard
        score(f"guard={guard} share={share}")
