from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agentcrawl import AgentCrawl, ScrapeDocument, __version__

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "quality"
MINIMUM_SCORE = 85

EXPECTED_BY_FIXTURE: dict[str, tuple[str, ...]] = {
    "adversarial_api_reference": (
        "Batch Scrape API Reference",
        "```bash",
        "| Parameter",
        "```json",
        "Stable error envelopes",
    ),
    "adversarial_media_news": (
        "Civic Sensor Network Publishes Open Field Data",
        "By Alina Reyes",
        "Portable stations recorded humidity",
        "| Metric",
        "```json",
    ),
    "adversarial_docs": (
        "Adversarial SDK Manual",
        "deterministic checkpoints",
        "```python",
        "| Signal",
        "Checkpoint timeline keeps recovery explainable",
    ),
    "adversarial_product": (
        "Signal Pack Pro",
        "$399.00",
        "| Variant",
        "Battery life held through a twelve hour field test",
    ),
    "documentation": ("Agent SDK Guide", "reliable agent workflows", "```python"),
    "article": ("City Launches Open Data Portal", "By Maya Torres", "weekly updates"),
    "ecommerce": ("TrailRunner Pro Backpack", "$129.00", "In stock"),
    "forum": ("Database Migration Discussion", "Original post by alex", "SQLite online backup"),
    "table": ("Regional Sales Table", "| Region", "$120,000"),
    "blog": ("How Agents Read The Web", "By Irene Shaw", "fixed fixtures"),
    "canonical": ("Canonical Deployment Guide", "authenticated stats endpoint", "rollback"),
    "messy_docs": ("Install Agent Runner", "isolated agent workflows", "agent-runner doctor"),
    "noisy_article": (
        "Transit Agency Opens Realtime Feed",
        "By Lena Ortiz",
        "stable JSON endpoints",
    ),
    "complex_product": ("Field Recorder Kit", "$249.00", "128GB"),
    "api_reference": ("Extraction API Reference", "```python", "| Parameter", "```json"),
    "media_article": (
        "Researchers Publish Open Climate Archive",
        "Coastal sensors",
        "Stable provenance",
    ),
    "nested_sidebar_docs": ("Agent Session Recovery", "crawler.crawl", "| Field"),
    "spa_rendered": ("Rendered Agent Dashboard", "active crawl workers", "| Metric", "```json"),
    "technical_reference_index": (
        "Protocol Semantics Reference",
        "message semantics",
        "stable provenance",
        "```http",
        "| Field",
    ),
    "cookie_consent": (
        "Authenticating With API Keys",
        "Generate a key on the dashboard",
        "JSON. The 200 response",
        "X-RateLimit-Remaining",
    ),
}

EXCLUDED_BY_FIXTURE: dict[str, tuple[str, ...]] = {
    "adversarial_api_reference": (
        "Product Pricing Login",
        "Legacy SDK",
        "Enterprise migration",
        "hosted batch dashboard",
        "Hidden duplicate Batch Scrape API Reference",
        "Footer legal archive",
    ),
    "adversarial_media_news": (
        "News Home Subscribe Advertise",
        "Celebrity weather myths",
        "Sponsored smart-city vendor ranking",
        "Subscribe to our sponsor briefing",
        "Hidden duplicate Civic Sensor Network",
        "Footer archive newsletters",
    ),
    "adversarial_docs": (
        "Pricing",
        "Billing",
        "Legacy crawler migration checklist",
        "Enterprise upgrade banner",
        "Hidden accessibility duplicate",
        "Subscribe to the hosted dashboard newsletter",
        "Footer documentation archive",
    ),
    "adversarial_product": (
        "Flash sale navigation",
        "Customers also viewed",
        "Unrelated drone bundle",
        "Clearance microphone cable",
        "Subscribe for limited drops",
        "Hidden duplicate Signal Pack Pro",
        "Download our app footer",
    ),
    "documentation": ("Pricing", "related promotional article", "Copyright Footer"),
    "article": ("Accept all cookies", "newsletter popup", "Advertise"),
    "ecommerce": ("Limited time promo", "unrelated upsell", "Account"),
    "forum": ("Forum Home", "Share buttons"),
    "table": ("Analytics Portal Navigation", "Download app footer"),
    "blog": ("Home Archive", "weekly AI newsletter"),
    "canonical": ("Billing", "Legacy footer"),
    "messy_docs": ("Pricing", "weekly newsletter popup", "related promotional article"),
    "noisy_article": (
        "Sponsored cloud analytics",
        "Subscribe to our daily briefing",
        "Advertise with us",
    ),
    "complex_product": ("Customers also bought", "Download app footer", "Clearance"),
    "api_reference": ("Pricing", "Legacy SDK", "Hidden accessibility duplicate", "Footer legal"),
    "media_article": ("Advertise", "celebrity weather myths", "Newsletter signup"),
    "nested_sidebar_docs": (
        "Login Pricing",
        "Sidebar upgrade promo",
        "Legacy crawler migration checklist",
        "Enterprise upgrade banner",
        "Footer documentation archive",
    ),
    "spa_rendered": ("Upgrade to the hosted dashboard", "Enable JavaScript"),
    "technical_reference_index": (
        "1xx Status Code 1",
        "79xx Status Code 79",
        "Footer archive legal",
    ),
    "cookie_consent": (
        "This site uses cookies",
        "we use cookies",
        "manage cookies",
        "consent to cookies",
        "by continuing you accept",
        "Footer legal archive",
    ),
}

REQUIRED_METADATA_BY_FIXTURE: dict[str, tuple[str, ...]] = {
    "complex_product": (
        "jsonld_count",
        "schema_types",
        "product_name",
        "product_price",
        "product_currency",
    ),
    "adversarial_product": (
        "jsonld_count",
        "schema_types",
        "product_name",
        "product_sku",
        "product_brand",
        "product_price",
        "product_currency",
        "product_availability",
        "product_rating_value",
        "product_review_count",
    ),
}

REQUIRED_METADATA = (
    "source_url",
    "final_url",
    "content_format",
    "markdown_chars",
    "text_chars",
    "link_count",
    "extraction_strategy",
    "selected_content_hint",
    "selected_content_tag",
    "candidate_count",
    "selected_content_score",
    "content_sha256",
    "heading_count",
    "fenced_code_block_count",
    "table_row_count",
)


@dataclass
class FixtureResult:
    name: str
    passed: bool
    score: int
    markdown_chars: int
    text_chars: int
    link_count: int
    missing: list[str]
    boilerplate_found: list[str]
    missing_metadata: list[str]
    structure_errors: list[str]
    quality_checks: dict[str, bool]
    errors: list[str]


def run_quality_report() -> dict[str, object]:
    crawler = AgentCrawl({"fetcher": "http"})
    results: list[FixtureResult] = []
    for fixture in sorted(FIXTURE_DIR.glob("*.html")):
        if fixture.stem not in EXPECTED_BY_FIXTURE:
            continue
        doc = crawler.scrape(str(fixture))
        if not isinstance(doc, ScrapeDocument):
            results.append(
                FixtureResult(
                    name=fixture.stem,
                    passed=False,
                    score=0,
                    markdown_chars=0,
                    text_chars=0,
                    link_count=0,
                    missing=[],
                    boilerplate_found=[],
                    missing_metadata=list(REQUIRED_METADATA),
                    structure_errors=["scrape did not return markdown"],
                    quality_checks={
                        "expected_content": False,
                        "metadata": False,
                        "boilerplate_removed": False,
                        "provenance": False,
                    },
                    errors=["scrape did not return ScrapeDocument"],
                )
            )
            continue
        results.append(_score_fixture(fixture.stem, doc))
    passed_count = sum(1 for result in results if result.passed)
    scores = [result.score for result in results]
    return {
        "agentcrawl_version": __version__,
        "fixture_count": len(results),
        "minimum_score": MINIMUM_SCORE,
        "average_score": round(sum(scores) / max(1, len(scores)), 2),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": [asdict(result) for result in results],
    }


def _score_fixture(name: str, doc: ScrapeDocument) -> FixtureResult:
    expected = EXPECTED_BY_FIXTURE.get(name, ())
    excluded = EXCLUDED_BY_FIXTURE.get(name, ())
    missing = [text for text in expected if text not in doc.markdown]
    boilerplate_found = [text for text in excluded if text in doc.markdown]
    missing_metadata = [
        key
        for key in (*REQUIRED_METADATA, *REQUIRED_METADATA_BY_FIXTURE.get(name, ()))
        if key not in doc.metadata or doc.metadata[key] in {None, ""}
    ]
    structure_errors = _markdown_structure_errors(doc.markdown)
    provenance_ok = bool(doc.metadata.get("source_url")) and bool(
        doc.metadata.get("selected_content_hint")
    )
    quality_checks = {
        "expected_content": not missing and bool(doc.markdown.strip()),
        "metadata": not missing_metadata,
        "boilerplate_removed": not boilerplate_found,
        "provenance": provenance_ok,
        "markdown_structure": not structure_errors,
    }
    score = _quality_score(quality_checks, doc.errors)
    passed = doc.ok and score >= MINIMUM_SCORE
    return FixtureResult(
        name=name,
        passed=passed,
        score=score,
        markdown_chars=len(doc.markdown),
        text_chars=len(doc.text),
        link_count=len(doc.links),
        missing=missing,
        boilerplate_found=boilerplate_found,
        missing_metadata=missing_metadata,
        structure_errors=structure_errors,
        quality_checks=quality_checks,
        errors=doc.errors,
    )


def _markdown_structure_errors(markdown: str) -> list[str]:
    errors: list[str] = []
    fence_count = sum(1 for line in markdown.splitlines() if line.startswith("```"))
    if fence_count % 2:
        errors.append("unbalanced fenced code blocks")
    table_lines = [line for line in markdown.splitlines() if line.strip().startswith("|")]
    if table_lines and not any("---" in line for line in table_lines):
        errors.append("table rows found without a Markdown separator row")
    return errors


def _quality_score(checks: dict[str, bool], errors: list[str]) -> int:
    score = 100
    if not checks["expected_content"]:
        score -= 35
    if not checks["boilerplate_removed"]:
        score -= 25
    if not checks["metadata"]:
        score -= 20
    if not checks["provenance"]:
        score -= 10
    if not checks.get("markdown_structure", False):
        score -= 15
    if errors:
        score -= 10
    return max(0, score)


def main() -> int:
    report: dict[str, Any] = run_quality_report()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
