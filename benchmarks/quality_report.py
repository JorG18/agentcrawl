from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agentcrawl import AgentCrawl, ScrapeDocument, __version__

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "quality"

EXPECTED_BY_FIXTURE: dict[str, tuple[str, ...]] = {
    "documentation": ("Agent SDK Guide", "reliable agent workflows", "```python"),
    "article": ("City Launches Open Data Portal", "By Maya Torres", "weekly updates"),
    "ecommerce": ("TrailRunner Pro Backpack", "$129.00", "In stock"),
    "forum": ("Database Migration Discussion", "Original post by alex", "SQLite online backup"),
    "table": ("Regional Sales Table", "| Region", "$120,000"),
    "blog": ("How Agents Read The Web", "By Irene Shaw", "fixed fixtures"),
    "canonical": ("Canonical Deployment Guide", "authenticated stats endpoint", "rollback"),
}


@dataclass
class FixtureResult:
    name: str
    passed: bool
    markdown_chars: int
    text_chars: int
    link_count: int
    missing: list[str]
    errors: list[str]


def run_quality_report() -> dict[str, object]:
    crawler = AgentCrawl({"fetcher": "http"})
    results: list[FixtureResult] = []
    for fixture in sorted(FIXTURE_DIR.glob("*.html")):
        doc = crawler.scrape(str(fixture))
        if not isinstance(doc, ScrapeDocument):
            results.append(
                FixtureResult(
                    name=fixture.stem,
                    passed=False,
                    markdown_chars=0,
                    text_chars=0,
                    link_count=0,
                    missing=[],
                    errors=["scrape did not return ScrapeDocument"],
                )
            )
            continue
        expected = EXPECTED_BY_FIXTURE.get(fixture.stem, ())
        missing = [text for text in expected if text not in doc.markdown]
        passed = doc.ok and not missing and bool(doc.markdown.strip())
        results.append(
            FixtureResult(
                name=fixture.stem,
                passed=passed,
                markdown_chars=len(doc.markdown),
                text_chars=len(doc.text),
                link_count=len(doc.links),
                missing=missing,
                errors=doc.errors,
            )
        )
    passed_count = sum(1 for result in results if result.passed)
    return {
        "agentcrawl_version": __version__,
        "fixture_count": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    report = run_quality_report()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
