"""Score a live web run of the neutral corpus pages against their reviewed signals.

Hosted APIs cannot read our snapshots, so for them quality is measured on a
live run (``sample_path=benchmarks/corpus/live_quality.json`` in the web
benchmark workflow) with the same signals and scoring as ``benchmarks.compare``.
A page a tool returned nothing for counts against ``got``, not against its
recall or noise; a page the tool never tried (skipped, or HTTP 402 for lack
of credits) is left out.

Usage::

    python -m benchmarks.web.live_quality --run RUN_DIR [--prefix drawn] [--skip TOOL]
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

from benchmarks.compare import score

from .aggregate import _not_attempted, mcnemar_p
from .report import load

MANIFEST = Path(__file__).resolve().parents[1] / "corpus" / "neutral.json"


def live_quality(run_dir: Path, manifest: Path = MANIFEST, prefix: str = "") -> dict[str, dict]:
    signals = {
        page["id"]: page
        for page in json.loads(Path(manifest).read_text("utf-8"))["pages"]
        if page.get("expected") and page["id"].startswith(prefix)
    }
    results = load(sorted(str(p) for p in Path(run_dir).rglob("*.jsonl.gz")))
    summary: dict[str, dict] = {}
    for tool, by_page in results.items():
        pages = [pid for pid in signals if not _not_attempted(by_page.get(pid) or {})]
        rows = []
        per_page = {}
        for pid in pages:
            markdown = (by_page.get(pid) or {}).get("markdown") or ""
            per_page[pid] = 0.0
            if markdown.strip():
                page = signals[pid]
                row = score(
                    tool,
                    pid,
                    markdown,
                    0,
                    expected=page["expected"],
                    excluded=page.get("excluded", ()),
                )
                rows.append(row)
                per_page[pid] = row.text_recall * (1 - row.leakage)
        summary[tool] = {
            "pages": len(pages),
            "got": len(rows),
            "text_recall": round(statistics.mean(r.text_recall for r in rows), 3) if rows else None,
            "noise": round(statistics.mean(r.leakage for r in rows), 3) if rows else None,
            "clean": sum(
                r.text_recall == 1.0 and r.structure_recall in (None, 1.0) and r.leakage == 0.0
                for r in rows
            ),
            "real_tokens": sum(r.real_tokens or 0 for r in rows),
            "quality": round(statistics.mean(r.text_recall * (1 - r.leakage) for r in rows), 3)
            if rows
            else None,
            "per_page": per_page,
        }
    return summary


def pairs(per_page: dict[str, dict[str, float]]) -> dict[str, dict]:
    """Per pair of tools: pages where each one's recall × (1 − noise) was higher, sign test."""
    out = {}
    for a, b in itertools.combinations(sorted(per_page), 2):
        common = set(per_page[a]) & set(per_page[b])
        a_better = sum(per_page[a][p] > per_page[b][p] for p in common)
        b_better = sum(per_page[b][p] > per_page[a][p] for p in common)
        out[f"{a} vs {b}"] = {
            "a_better": a_better,
            "b_better": b_better,
            "same": len(common) - a_better - b_better,
            "p": round(mcnemar_p(a_better, b_better), 4),
        }
    return out


def markdown(summary: dict[str, dict]) -> str:
    lines = [
        "| tool | got | text recall | noise | clean | tokens (o200k) | quality |",
        "|---|---|---|---|---|---|---|",
    ]
    for tool, row in sorted(summary.items(), key=lambda kv: -(kv[1]["quality"] or 0)):
        lines.append(
            f"| {tool} | {row['got']}/{row['pages']} | {row['text_recall']} | {row['noise']} "
            f"| {row['clean']} | {row['real_tokens']} | {row['quality']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", required=True, help="The live run's folder of *.jsonl.gz.")
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--prefix", default="", help="Only pages whose id starts with this.")
    parser.add_argument("--skip", action="append", default=[], help="Leave this tool out.")
    parser.add_argument("--out", help="Write the summary as JSON here.")
    args = parser.parse_args(argv)
    summary = live_quality(Path(args.run), Path(args.manifest), args.prefix)
    summary = {tool: row for tool, row in summary.items() if tool not in args.skip}
    compared = pairs({tool: row["per_page"] for tool, row in summary.items()})
    if args.out:
        out = {"tools": summary, "pairs": compared}
        Path(args.out).write_text(json.dumps(out, indent=2) + "\n", "utf-8")
    print(markdown(summary))
    print("\n| pair | first better | second better | same | p (sign test) |\n|---|---|---|---|---|")
    for name, pair in compared.items():
        print(
            f"| {name} | {pair['a_better']} | {pair['b_better']} | {pair['same']} | {pair['p']} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
