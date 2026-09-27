"""TEMPORARY (removed before merge): diagnose recall misses on captured HTML."""

import gzip
import json
import re
import statistics
from collections import Counter

from agentcrawl import parsing
from agentcrawl.challenge import html_to_plain_text
from agentcrawl.config import CrawlConfig
from benchmarks.compare import _plain as parsing_plain
from benchmarks.web.report import classify, sentences


def load(path):
    return {r["id"]: r for r in map(json.loads, gzip.open(path, "rt"))}


cap = load("cap/agentcrawl.jsonl.gz")
refs = {t: load(f"ref/{t}.jsonl.gz") for t in ("crawl4ai", "firecrawl", "tavily")}
kinds = {p["id"]: p["kind"] for p in json.load(open("ref/sample.json"))["pages"]}
cfg = CrawlConfig()


def why_dropped(html, probe):
    """Ancestors of the deepest node holding ``probe``, with the rule that drops them."""
    parser = parsing._HTMLTreeParser()
    parser.feed(html)
    parser.close()
    path = []

    def find(node, trail):
        for child in node.children:
            if isinstance(child, parsing._HTMLNode):
                if probe in norm(parsing._node_text(child)):
                    return find(child, trail + [child]) or trail + [child]
        return None

    path = find(parser.root, []) or []
    reasons = []
    for node in path:
        ident = parsing._node_identity(node).strip()[:50]
        if node.tag in parsing._ALWAYS_REMOVE_TAGS:
            reasons.append(f"removed-tag:{node.tag}")
        if parsing._is_hidden(node):
            attr = (
                "aria"
                if node.attr("aria-hidden").lower() == "true"
                else (
                    "hidden-attr"
                    if node.attr("hidden")
                    else (
                        "style"
                        if "none" in node.attr("style") or "hidden" in node.attr("style")
                        else "class"
                    )
                )
            )
            reasons.append(f"hidden:{attr}:{node.tag}:{ident}")
        if parsing._is_boilerplate(node):
            word = parsing._BOILERPLATE_HINTS.search(parsing._node_identity(node))
            reasons.append(f"bp-class:{word.group(0).lower() if word else '?'}:{ident}")
        if node.tag in parsing._BOILERPLATE_TAGS:
            reasons.append(f"bp-tag:{node.tag}")
        if parsing._is_index_node(node):
            reasons.append("index")
        if parsing._is_cookie_consent_text(node):
            reasons.append("cookie")
    return reasons or ["?"]


reason_counts = Counter()


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[#>|*_`]", " ", text)).strip().lower()


errs = Counter()
for r in cap.values():
    if r.get("error"):
        errs[(r.get("error_type"), re.sub(r"https?://\S+", "URL", r["error"])[:90])] += 1
for (kind, msg), n in errs.most_common(25):
    print(f"ERR {n:3d} {kind}: {msg}")
for r in cap.values():
    if r.get("browser_fallback_error") or r.get("browser_render_error"):
        print(
            "FALLBACK",
            r["url"][:40],
            (r.get("browser_fallback_error") or r.get("browser_render_error"))[:150],
        )
print("fetchers", Counter(r.get("fetcher") for r in cap.values()))
print("fallback_reason", Counter(r.get("fallback_reason") for r in cap.values()))
print(
    "waited",
    [
        (r["url"][:30], r.get("challenge_waited_ms"))
        for r in cap.values()
        if r.get("challenge_waited_ms")
    ],
)
causes = Counter()
examples = []

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
    plain_md = norm(parsing_plain(md))
    for sentence in missed:
        probe = sentence[:40]
        if sentence in everything:
            page_causes["dropped_by_extraction"] += 1
        elif probe in whole:
            page_causes["in_html_but_split_differently"] += 1
            if probe[:25] not in plain_md:
                rs = why_dropped(html, probe[:30])
                reason_counts.update(
                    {r.split(":")[0] + ":" + r.split(":")[1] if ":" in r else r for r in rs}
                )
                if len(examples) < 25:
                    examples.append((pid, sentence[:100], " | ".join(rs)))
            if False:
                at = plain_md.find(probe[:25])
                examples.append(
                    (
                        pid,
                        sentence[:160],
                        plain_md[max(0, at - 60) : at + 200] if at >= 0 else "<not in md>",
                    )
                )
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
print("drop reasons:", reason_counts.most_common(20))
for pid, sentence, around in examples:
    print("--", pid, "\n   REF:", sentence, "\n   OUR:", around.replace("\n", " / "))
for row in rows[:5]:
    print(f"{row[0]:.2f} {row[1][:40]:40s} {row[2]:8s} {row[3]} n={row[4]} {row[5]}")


def score():
    recalls, chars = [], []
    for pid, r in cap.items():
        html = r.get("html") or ""
        if r.get("error") or not html:
            continue
        md = parsing.html_to_markdown(html, cfg, base_url=r["url"])
        chars.append(len(md))
        per = [
            sentences(ref[pid]["markdown"])
            for ref in refs.values()
            if pid in ref and classify(ref[pid]) == "content"
        ]
        if len(per) < 2:
            continue
        counts = Counter(s for found in per for s in found)
        cons = {s for s, n in counts.items() if n >= 2}
        if len(cons) >= 3:
            recalls.append(len(cons & sentences(md)) / len(cons))
    return statistics.mean(recalls), statistics.median(chars), sum(chars)


for soft in ("off", "links"):
    for hidden in ("hide", "aria"):
        for share in (0, 0.5):
            parsing._SOFT_MODE, parsing._HIDDEN_MODE, parsing._HOMEPAGE_SHARE = soft, hidden, share
            rec, med, tot = score()
            print(
                f"VARIANT soft={soft} hidden={hidden} share={share}: "
                f"recall={rec:.3f} median_chars={med:.0f} total_chars={tot}"
            )
