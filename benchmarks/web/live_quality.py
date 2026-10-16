"""Score a live web run of the neutral corpus pages against their reviewed signals.

Hosted APIs cannot read our snapshots, so for them quality is measured on a
live run (``sample_path=benchmarks/corpus/live_quality.json`` in the web
benchmark workflow) with the same signals and scoring as ``benchmarks.compare``.
A page a tool returned nothing for counts against ``got``, not against its
recall or noise; a page refused for lack of credits (HTTP 402) is left out.

Usage::

    python -m benchmarks.web.live_quality --run RUN_DIR [--prefix drawn] [--skip TOOL]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from benchmarks.compare import score

from .aggregate import _billing_error
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
        pages = [pid for pid in signals if not _billing_error(by_page.get(pid) or {})]
        rows = []
        for pid in pages:
            markdown = (by_page.get(pid) or {}).get("markdown") or ""
            if markdown.strip():
                page = signals[pid]
                rows.append(
                    score(
                        tool,
                        pid,
                        markdown,
                        0,
                        expected=page["expected"],
                        excluded=page.get("excluded", ()),
                    )
                )
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
        }
    return summary


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
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    print(markdown(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
