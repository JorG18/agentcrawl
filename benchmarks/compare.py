"""Reproducible, offline extraction benchmark: AgentCrawl vs other extractors.

Lane: *clean Markdown extraction* over the committed quality fixtures
(``tests/fixtures/quality``). Every tool gets the same raw HTML string — no
network, no API keys, no live sites — so anyone can re-run it and get the same
numbers for the same tool versions.

Per fixture and tool it measures:

- **text recall** — share of the expected *text* signals present;
- **structure recall** — share of expected Markdown *structure* kept: a table
  signal counts when its cell text sits on a pipe-table row in any valid style
  (``| a | b |`` or ``a| b``), a code-fence signal counts when a fence exists.
  Reported separately so a tool that keeps the words but drops tables is
  visible, and a tool is never penalised on text for a formatting choice;
- **fence language** — share of expected code fences that kept their language
  tag (```` ```python ````). Useful to agents, but a stricter bar than "kept the
  code", so it is its own column instead of hiding inside structure recall;
- **noise leakage** — share of known boilerplate strings that leaked through
  (navigation, promos, cookie banners, hidden duplicates);
- **tokens** — ``estimate_tokens`` of the output (lower is better only when
  recall holds);
- **latency** — wall time of the extraction call (browser start-up excluded).

Matching is case-sensitive substring search after collapsing whitespace.

Adapters run only when their package is importable; missing tools are listed
as ``skipped`` rather than silently dropped. Hosted APIs (Firecrawl, Jina
Reader, ScrapeGraphAI) need network and keys and are *not* part of this lane.

Usage::

    python -m benchmarks.compare                       # table to stdout
    python -m benchmarks.compare --json out.json       # plus raw results
    python -m benchmarks.compare --tools agentcrawl,trafilatura

The ROADMAP rule stands: publish numbers only together with this script, the
fixture set and the tool versions it printed.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

from agentcrawl.utils import estimate_tokens

from .quality_report import EXCLUDED_BY_FIXTURE, EXPECTED_BY_FIXTURE, FIXTURE_DIR

Extractor = Callable[[str], str]


@dataclass
class Row:
    tool: str
    fixture: str
    text_recall: float
    structure_recall: float | None
    fence_language: float | None
    leakage: float
    tokens: int
    latency_ms: float
    missing: list[str] = field(default_factory=list)
    leaked: list[str] = field(default_factory=list)
    error: str | None = None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _is_structure(signal: str) -> bool:
    return signal.startswith("|") or signal.startswith("```")


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------


def _agentcrawl() -> Extractor:
    from agentcrawl.config import CrawlConfig
    from agentcrawl.parsing import html_to_markdown

    config = CrawlConfig()
    return lambda html: html_to_markdown(html, config, only_main_content=True)


def _html2text_baseline() -> Extractor:
    import html2text

    def run(html: str) -> str:
        converter = html2text.HTML2Text()
        converter.body_width = 0
        return converter.handle(html)

    return run


def _trafilatura() -> Extractor:
    import trafilatura

    return lambda html: (
        trafilatura.extract(
            html,
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=False,
        )
        or ""
    )


class _Crawl4AI:
    """One browser for the whole run, so start-up cost is not in the latency."""

    def __init__(self, fit: bool) -> None:
        import asyncio

        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

        try:  # 0.9+: the lxml filter replaces the deprecated BeautifulSoup one
            from crawl4ai.content_filter_strategy import (
                PruningContentFilterLXML as PruningContentFilter,
            )
        except ImportError:
            from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        self._fit = fit
        self._loop = asyncio.new_event_loop()
        self._crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
        self._loop.run_until_complete(self._crawler.start())
        generator = (
            DefaultMarkdownGenerator(content_filter=PruningContentFilter())
            if fit
            else DefaultMarkdownGenerator()
        )
        self._run_config = CrawlerRunConfig(
            markdown_generator=generator, cache_mode=CacheMode.BYPASS, verbose=False
        )

    def __call__(self, html: str) -> str:
        result = self._loop.run_until_complete(
            self._crawler.arun(url="raw:" + html, config=self._run_config)
        )
        markdown = result.markdown
        if markdown is None:
            return ""
        if self._fit:
            return getattr(markdown, "fit_markdown", "") or ""
        return getattr(markdown, "raw_markdown", None) or str(markdown)

    def close(self) -> None:
        self._loop.run_until_complete(self._crawler.close())
        self._loop.close()


ADAPTERS: dict[str, tuple[str, Callable[[], Extractor]]] = {
    "agentcrawl": ("agentcrawl-ai", _agentcrawl),
    "html2text-baseline": ("html2text", _html2text_baseline),
    "trafilatura": ("trafilatura", _trafilatura),
    "crawl4ai": ("crawl4ai", lambda: _Crawl4AI(fit=False)),
    "crawl4ai-fit": ("crawl4ai", lambda: _Crawl4AI(fit=True)),
}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _structure_present(signal: str, markdown: str, table_lines: list[str]) -> bool:
    if signal.startswith("```"):
        return "```" in markdown or "~~~" in markdown
    cell = signal.lstrip("|").strip()
    return any(cell in line for line in table_lines)


def score(tool: str, fixture: str, markdown: str, latency_ms: float) -> Row:
    output = _norm(markdown)
    expected = EXPECTED_BY_FIXTURE[fixture]
    text_signals = [signal for signal in expected if not _is_structure(signal)]
    structure_signals = [signal for signal in expected if _is_structure(signal)]
    table_lines = [line for line in markdown.splitlines() if line.count("|") >= 1]
    missing = [signal for signal in text_signals if _norm(signal) not in output] + [
        signal
        for signal in structure_signals
        if not _structure_present(signal, markdown, table_lines)
    ]
    fences = [signal for signal in expected if signal.startswith("```") and len(signal) > 3]
    fence_language = (
        sum(1 for signal in fences if signal in markdown) / len(fences) if fences else None
    )
    excluded = EXCLUDED_BY_FIXTURE.get(fixture, ())
    leaked = [signal for signal in excluded if _norm(signal) in output]

    def recall(signals: list[str]) -> float:
        return sum(1 for signal in signals if signal not in missing) / len(signals)

    return Row(
        tool=tool,
        fixture=fixture,
        text_recall=recall(text_signals) if text_signals else 1.0,
        structure_recall=recall(structure_signals) if structure_signals else None,
        fence_language=fence_language,
        leakage=len(leaked) / len(excluded) if excluded else 0.0,
        tokens=estimate_tokens(markdown),
        latency_ms=latency_ms,
        missing=missing,
        leaked=leaked,
    )


def run(tools: list[str]) -> tuple[list[Row], dict[str, str], dict[str, str]]:
    fixtures = sorted(EXPECTED_BY_FIXTURE)
    rows: list[Row] = []
    versions: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for tool in tools:
        package, factory = ADAPTERS[tool]
        try:
            extractor = factory()
        except Exception as exc:  # not installed, or its runtime is missing
            skipped[tool] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        try:
            versions[tool] = version(package)
        except PackageNotFoundError:
            versions[tool] = "source checkout"
        try:
            for fixture in fixtures:
                html = (FIXTURE_DIR / f"{fixture}.html").read_text(encoding="utf-8")
                started = time.perf_counter()
                try:
                    markdown = extractor(html)
                    error = None
                except Exception as exc:
                    markdown, error = "", f"{type(exc).__name__}: {exc}"[:300]
                elapsed = (time.perf_counter() - started) * 1000
                row = score(tool, fixture, markdown, elapsed)
                row.error = error
                rows.append(row)
        finally:
            close = getattr(extractor, "close", None)
            if callable(close):
                close()
    return rows, versions, skipped


def summarize(rows: list[Row]) -> list[dict[str, object]]:
    summary = []
    for tool in dict.fromkeys(row.tool for row in rows):
        mine = [row for row in rows if row.tool == tool]
        structure = [row.structure_recall for row in mine if row.structure_recall is not None]
        languages = [row.fence_language for row in mine if row.fence_language is not None]
        summary.append(
            {
                "tool": tool,
                "fixtures": len(mine),
                "text_recall": round(statistics.mean(row.text_recall for row in mine), 3),
                "structure_recall": round(statistics.mean(structure), 3) if structure else None,
                "fence_language": round(statistics.mean(languages), 3) if languages else None,
                "noise_leakage": round(statistics.mean(row.leakage for row in mine), 3),
                "clean_passes": sum(
                    1
                    for row in mine
                    if row.text_recall == 1.0
                    and (row.structure_recall in (None, 1.0))
                    and row.leakage == 0.0
                ),
                "total_tokens": sum(row.tokens for row in mine),
                "median_latency_ms": round(statistics.median(row.latency_ms for row in mine), 1),
                "errors": sum(1 for row in mine if row.error),
            }
        )
    return summary


def _markdown_table(summary: list[dict[str, object]]) -> str:
    headers = [
        "tool",
        "text recall",
        "structure recall",
        "fence language",
        "noise leakage",
        "clean passes",
        "total tokens",
        "median ms",
        "errors",
    ]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for item in summary:
        structure = item["structure_recall"]
        language = item["fence_language"]
        lines.append(
            "| "
            + " | ".join(
                str(value)
                for value in (
                    item["tool"],
                    f"{item['text_recall']:.1%}",
                    "—" if structure is None else f"{structure:.1%}",
                    "—" if language is None else f"{language:.1%}",
                    f"{item['noise_leakage']:.1%}",
                    f"{item['clean_passes']}/{item['fixtures']}",
                    f"{item['total_tokens']:,}",
                    item["median_latency_ms"],
                    item["errors"],
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _environment() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        commit, dirty = "", ""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "agentcrawl_commit": (commit + ("+dirty" if dirty else "")) or "unknown",
        "fixture_dir": str(FIXTURE_DIR.relative_to(root)),
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tools", default=",".join(ADAPTERS), help="Comma-separated adapters.")
    parser.add_argument("--json", dest="json_path", help="Write full per-fixture results here.")
    args = parser.parse_args(argv)
    tools = [tool.strip() for tool in args.tools.split(",") if tool.strip()]
    unknown = sorted(set(tools) - set(ADAPTERS))
    if unknown:
        parser.error(f"unknown tools: {', '.join(unknown)}; choose from {', '.join(ADAPTERS)}")

    rows, versions, skipped = run(tools)
    summary = summarize(rows)
    environment = _environment()
    print(_markdown_table(summary))
    print()
    print("versions: " + ", ".join(f"{tool}={ver}" for tool, ver in versions.items()))
    for tool, reason in skipped.items():
        print(f"skipped {tool}: {reason}")
    print("environment: " + json.dumps(environment))
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {
                    "environment": environment,
                    "versions": versions,
                    "skipped": skipped,
                    "summary": summary,
                    "rows": [asdict(row) for row in rows],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
