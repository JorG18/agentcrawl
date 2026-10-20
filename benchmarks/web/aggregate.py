"""Pool several web benchmark runs and say whether a difference is real.

One run of 400 pages moves by a few pages from one day to the next (anti-bot
verdicts, slow sites), so a single run cannot tell a 5-page gap from noise.
This pools runs of the same sample and, for every pair of tools, counts the
(page, run) pairs where only one of them got content. An exact McNemar test on
those counts gives ``p``; ``band`` is how much the per-run difference itself
moved between runs. A gap with ``p > 0.05`` is written as no significant difference.

Usage::

    python -m benchmarks.web.aggregate --run run1/ --run run2/ --out summary.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any

from .report import classify, load

ALPHA = 0.05


def mcnemar_p(a_only: int, b_only: int) -> float:
    """Exact two-sided McNemar test (binomial with p = 1/2 on the discordant pairs)."""
    n = a_only + b_only
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(a_only, b_only) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _content(result: dict[str, Any] | None) -> bool:
    return result is not None and classify(result) == "content"


def _not_attempted(result: dict[str, Any]) -> bool:
    """Skipped (no key, beyond --limit) or refused for lack of credits (HTTP 402)."""
    # The hosted adapters' own prefix: a page whose text mentions 402 was still tried.
    return bool(result.get("skipped")) or (result.get("error") or "").startswith(
        "RuntimeError: HTTP 402"
    )


def pooled(runs: list[dict[str, dict[str, dict[str, Any]]]]) -> dict[str, Any]:
    """``runs`` = one ``report.load`` result per run, all on the same sample.

    A page a tool never tried is left out of that tool's run: skipped (no key,
    beyond --limit) or refused for lack of credits (HTTP 402, the account ran
    dry). Counting it would charge the tool for a page it never saw.
    """
    runs = [
        {
            tool: {page: r for page, r in results.items() if not _not_attempted(r)}
            for tool, results in run.items()
        }
        for run in runs
    ]
    tools = sorted({tool for run in runs for tool in run})
    summary: dict[str, Any] = {"runs": len(runs), "tools": {}, "pairs": {}}
    for tool in tools:
        per_run = [sum(_content(r) for r in run.get(tool, {}).values()) for run in runs]
        summary["tools"][tool] = {
            "content_per_run": per_run,
            "content_total": sum(per_run),
            "pages": sum(len(run.get(tool, {})) for run in runs),
        }
    for a, b in itertools.combinations(tools, 2):
        a_only = b_only = 0
        differences = []
        for run in runs:
            pages = set(run.get(a, {})) & set(run.get(b, {}))
            got_a = {page for page in pages if _content(run[a][page])}
            got_b = {page for page in pages if _content(run[b][page])}
            a_only += len(got_a - got_b)
            b_only += len(got_b - got_a)
            differences.append(len(got_a) - len(got_b))
        summary["pairs"][f"{a} vs {b}"] = {
            "a": a,
            "b": b,
            "a_only": a_only,
            "b_only": b_only,
            "difference": a_only - b_only,
            "p": round(mcnemar_p(a_only, b_only), 4),
            "band": max(differences) - min(differences) if differences else 0,
        }
    return summary


def _verdict(pair: dict[str, Any]) -> str:
    if pair["p"] > ALPHA:
        return "no significant difference"
    return f"{pair['a'] if pair['difference'] > 0 else pair['b']} ahead"


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"Runs pooled: {summary['runs']}",
        "",
        "| tool | content pages per run | total | pages tried |",
        "|---|---|---|---|",
    ]
    for tool, row in summary["tools"].items():
        per_run = ", ".join(map(str, row["content_per_run"]))
        lines.append(f"| {tool} | {per_run} | {row['content_total']} | {row['pages']} |")
    lines += [
        "",
        "| pair | only first | only second | difference | run-to-run band | p (McNemar) | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, pair in summary["pairs"].items():
        lines.append(
            f"| {name} | {pair['a_only']} | {pair['b_only']} | {pair['difference']:+d} "
            f"| {pair['band']} | {pair['p']} | {_verdict(pair)} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="A run's folder of *.jsonl.gz results (searched recursively).",
    )
    parser.add_argument("--out", help="Write the summary as JSON here.")
    parser.add_argument("--skip", action="append", default=[], help="Leave this tool out.")
    args = parser.parse_args(argv)
    runs = [load(sorted(str(p) for p in Path(folder).rglob("*.jsonl.gz"))) for folder in args.run]
    runs = [{t: r for t, r in run.items() if t not in args.skip} for run in runs]
    summary = pooled(runs)
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    print(markdown(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
