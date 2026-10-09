"""Compare tools on a web sample without hand-written expectations.

Every result is put in one of five buckets, the same way for every tool:

- ``content``: readable text (at least ``MIN_CONTENT_CHARS``) that does not
  read as a bot challenge or an error page;
- ``thin``: the tool returned something, but under ``MIN_CONTENT_CHARS``;
- ``junk``: the output reads as a challenge, captcha or access-denied page,
  i.e. an error returned as if it were content;
- ``failed``: the tool reported an error (AgentCrawl also says which kind);
- ``skipped``: the tool did not run (no API key).

Quality without an answer key: a sentence counts as *consensus content* of a
page when at least two tools returned it. Each tool's **consensus recall** is
the share of those sentences it returned, averaged over pages where at least
two tools got content. It rewards keeping what others agree is on the page
and does not reward padding; tokens are reported next to it.

Usage::

    python -m benchmarks.web.report --sample sample.json results/*.jsonl.gz --out report
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from agentcrawl.utils import estimate_tokens

from ..compare import _plain
from .run import PAGE_TIMEOUT_S

# Tools that run on the benchmark runner. Each stops a page its own way
# (AgentCrawl's page budget, Scrapling's per-step timeout that let a page run
# for minutes), so the same wall clock is applied to all of them here. Hosted
# APIs are left out: their time includes our rate-limit waits.
LOCAL_TOOLS = frozenset({"agentcrawl", "agentcrawl-camofox", "crawl4ai", "scrapling"})

MIN_CONTENT_CHARS = 300
MIN_SENTENCE_CHARS = 50
MIN_URL_LINES = 5
_URL_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?https?://\S+\s*$", re.M)

_JUNK_RE = re.compile(
    r"just a moment|verify(?:ing)? (?:that )?you are (?:a )?human|checking your browser|"
    r"enable javascript and cookies|are you a robot|press (?:&|and) hold|captcha|"
    r"access denied|access to this page has been denied|403 forbidden|request blocked|"
    r"unusual traffic|pardon our interruption|client challenge|attention required|"
    r"performing security verification|verif(?:y|ies) you are not a bot|"
    r"suspect (?:that )?you(?:[’']re| are) a (?:robot|bot)|you(?:[’']ve| have) been blocked|"
    # Cloudflare/edge error pages ("Web server is down", error 52x).
    r"web server is down|origin is unreachable|error code:? ?52\d|bad gateway|"
    r"service (?:temporarily )?unavailable",
    re.I,
)
# The reCAPTCHA notice in a form footer is on real pages, not block pages.
_RECAPTCHA_NOTICE_RE = re.compile(r"protected by recaptcha", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def classify(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return "skipped"
    if result.get("tool") in LOCAL_TOOLS and (result.get("seconds") or 0) > PAGE_TIMEOUT_S:
        return "failed"
    text = _plain(result.get("markdown") or "")
    readable = re.sub(r"\s+", " ", re.sub(r"https?://\S+|[#|>\-*_=\[\]()]", " ", text)).strip()
    if not readable:
        # A sitemap read as its list of URLs is the document, not a thin page.
        if not result.get("error") and len(_URL_LINE_RE.findall(text)) >= MIN_URL_LINES:
            return "content"
        return "failed" if result.get("error") else "thin"
    # Challenge wording on a short page is a challenge; a long article may quote it.
    if len(readable) < 3000 and _JUNK_RE.search(_RECAPTCHA_NOTICE_RE.sub(" ", readable)):
        return "junk"
    # An error page (404, 410…) is not the page, whatever its length.
    if (result.get("status_code") or 0) >= 400:
        return "failed"
    # Text returned next to an error the tool itself reported (a block page,
    # an error body) is not content.
    if result.get("error") and len(readable) < 3000:
        return "failed"
    if len(readable) < MIN_CONTENT_CHARS:
        return "thin"
    return "content"


def sentences(markdown: str) -> set[str]:
    text = _plain(markdown)
    out = set()
    for part in _SENTENCE_SPLIT_RE.split(text):
        norm = re.sub(r"\s+", " ", re.sub(r"[#>|*_`]", " ", part)).strip().lower()
        if len(norm) >= MIN_SENTENCE_CHARS:
            out.add(norm)
    return out


def load(paths: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """``{tool: {page_id: result}}``."""
    by_tool: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                result = json.loads(line)
                by_tool[result["tool"]][result["id"]] = result
    return dict(by_tool)


def analyse(
    sample: dict[str, Any], by_tool: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    pages = sample["pages"]
    tools = [tool for tool in by_tool if any(not r.get("skipped") for r in by_tool[tool].values())]
    buckets = {tool: {} for tool in tools}
    for tool in tools:
        for page in pages:
            result = by_tool[tool].get(page["id"], {"error": "missing"})
            buckets[tool][page["id"]] = classify(result)

    reachable = {
        page["id"] for page in pages if any(buckets[t][page["id"]] == "content" for t in tools)
    }
    # A tool limited to part of the sample is judged only on the pages it ran.
    ran = {tool: [p for p in pages if buckets[tool][p["id"]] != "skipped"] for tool in tools}
    recall: dict[str, list[float]] = defaultdict(list)
    consensus_pages = 0
    for page in pages:
        pid = page["id"]
        with_content = [t for t in tools if buckets[t][pid] == "content"]
        if len(with_content) < 2:
            continue
        per_tool = {t: sentences(by_tool[t][pid]["markdown"]) for t in with_content}
        counts = Counter(s for found in per_tool.values() for s in found)
        consensus = {s for s, n in counts.items() if n >= 2}
        if len(consensus) < 3:
            continue
        consensus_pages += 1
        for tool in tools:
            if buckets[tool][pid] == "skipped":
                continue
            found = per_tool.get(tool, set())
            recall[tool].append(len(found & consensus) / len(consensus))

    summary = []
    for tool in tools:
        counts = Counter(buckets[tool].values())
        own = ran[tool]
        results = [by_tool[tool].get(page["id"], {}) for page in own]
        content_results = [r for r, p in zip(results, own) if buckets[tool][p["id"]] == "content"]
        missed = sum(1 for p in own if p["id"] in reachable and buckets[tool][p["id"]] != "content")
        seconds = [r["seconds"] for r in results if isinstance(r.get("seconds"), (int, float))]
        error_types = Counter(
            r.get("error_type") or "unclassified"
            for r, p in zip(results, own)
            if buckets[tool][p["id"]] == "failed"
        )
        summary.append(
            {
                "tool": tool,
                "pages": len(own),
                "content": counts["content"],
                "thin": counts["thin"],
                "junk": counts["junk"],
                "failed": counts["failed"],
                "missed_reachable": missed,
                "consensus_recall": round(statistics.mean(recall[tool]), 3)
                if recall[tool]
                else None,
                "median_tokens": int(
                    statistics.median([estimate_tokens(r["markdown"]) for r in content_results])
                )
                if content_results
                else 0,
                "median_seconds": round(statistics.median(seconds), 2) if seconds else None,
                "failure_kinds": dict(error_types.most_common()),
            }
        )

    by_stratum: dict[str, dict[str, float]] = defaultdict(dict)
    strata = sorted(
        {p["stratum"] for p in pages},
        key=lambda s: (0, int(s.split("-")[0])) if s[0].isdigit() else (1, 0),
    )
    for stratum in strata:
        for tool in tools:
            subset = [p for p in ran[tool] if p["stratum"] == stratum]
            got = sum(1 for p in subset if buckets[tool][p["id"]] == "content")
            by_stratum[stratum][tool] = round(got / len(subset), 3) if subset else 0.0

    by_kind: dict[str, dict[str, float]] = defaultdict(dict)
    for kind in sorted({str(p.get("kind")) for p in pages}):
        for tool in tools:
            subset = [p for p in ran[tool] if p.get("kind") == kind]
            got = sum(1 for p in subset if buckets[tool][p["id"]] == "content")
            by_kind[kind][tool] = round(got / len(subset), 3) if subset else 0.0

    # Pages AgentCrawl missed while another tool got content: the to-do list.
    gaps = []
    if "agentcrawl" in tools:
        for page in pages:
            pid = page["id"]
            if pid in reachable and buckets["agentcrawl"][pid] != "content":
                result = by_tool["agentcrawl"].get(pid, {})
                gaps.append(
                    {
                        "url": page["url"],
                        "bucket": buckets["agentcrawl"][pid],
                        "error_type": result.get("error_type"),
                        "error": result.get("error"),
                        "got_content": [t for t in tools if buckets[t][pid] == "content"],
                    }
                )
    skipped = sorted(tool for tool in by_tool if tool not in tools)
    return {
        "skipped_tools": skipped,
        "sample": {k: v for k, v in sample.items() if k != "pages"},
        "pages": len(pages),
        "reachable_by_any_tool": len(reachable),
        "consensus_pages": consensus_pages,
        "summary": summary,
        "content_rate_by_rank": by_stratum,
        "content_rate_by_kind": by_kind,
        "agentcrawl_gaps": gaps,
        "buckets": buckets,
    }


def markdown_report(report: dict[str, Any], title: str = "Web sample") -> str:
    lines = [
        f"# {title}: {report['pages']} pages",
        "",
        f"Sample seed {report['sample'].get('seed')}, drawn {report['sample'].get('drawn_utc')}. "
        f"{report['reachable_by_any_tool']} pages gave content to at least one tool; "
        f"consensus recall uses the {report['consensus_pages']} pages where two or more did.",
        "",
        "| tool | pages | content | thin | junk | failed | missed (others got it) | consensus recall | median tokens | median s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["summary"]:
        recall = "n/a" if row["consensus_recall"] is None else f"{row['consensus_recall']:.1%}"
        lines.append(
            f"| {row['tool']} | {row['pages']} | {row['content']} | {row['thin']} | {row['junk']} | {row['failed']} "
            f"| {row['missed_reachable']} | {recall} | {row['median_tokens']} | {row['median_seconds']} |"
        )
    if report.get("skipped_tools"):
        lines.append(f"\nNot run (no API key): {', '.join(report['skipped_tools'])}")
    lines += ["", "Content rate by Tranco rank:", ""]
    tools = [row["tool"] for row in report["summary"]]
    lines.append("| rank | " + " | ".join(tools) + " |")
    lines.append("|---" * (len(tools) + 1) + "|")
    for stratum, rates in report["content_rate_by_rank"].items():
        lines.append(f"| {stratum} | " + " | ".join(f"{rates[t]:.0%}" for t in tools) + " |")
    for kind, rates in report["content_rate_by_kind"].items():
        lines.append(f"| {kind} | " + " | ".join(f"{rates[t]:.0%}" for t in tools) + " |")
    for row in report["summary"]:
        if row["failure_kinds"]:
            kinds = ", ".join(f"{k}: {v}" for k, v in row["failure_kinds"].items())
            lines.append(f"\n{row['tool']} failures: {kinds}")
    gaps = report["agentcrawl_gaps"]
    if gaps:
        lines += ["", f"## AgentCrawl gaps ({len(gaps)})", ""]
        reasons = Counter(g["error_type"] or g["bucket"] for g in gaps)
        lines.append(", ".join(f"{reason}: {count}" for reason, count in reasons.most_common()))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", nargs="+", help="JSONL.gz files from benchmarks.web.run")
    parser.add_argument("--sample", default="sample.json")
    parser.add_argument("--out", default="report", help="Writes <out>.json and <out>.md")
    args = parser.parse_args(argv)
    sample = json.loads(Path(args.sample).read_text("utf-8"))
    by_tool = load(args.results)
    # The random block is the headline; the hard block (page types chosen on
    # purpose) is reported on its own so it cannot move the headline number.
    blocks = {}
    for block in ("random", "hard"):
        pages = [p for p in sample["pages"] if p.get("block", "random") == block]
        if pages:
            blocks[block] = analyse({**sample, "pages": pages}, by_tool)
    report = blocks.get("random") or blocks["hard"]
    if "hard" in blocks and "random" in blocks:
        report = {**report, "hard_block": blocks["hard"]}
    Path(f"{args.out}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), "utf-8")
    text = markdown_report(report, "Random web sample")
    if "hard_block" in report:
        text += "\n" + markdown_report(report["hard_block"], "Hard block")
    Path(f"{args.out}.md").write_text(text, "utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
