from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agentcrawl import AgentCrawl, ScrapeDocument


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "quality"


@dataclass(frozen=True)
class QualityCase:
    name: str
    expected: tuple[str, ...]
    excluded: tuple[str, ...]
    metadata: tuple[str, ...] = ()


QUALITY_CASES = [
    QualityCase(
        name="documentation",
        expected=(
            "Agent SDK Guide",
            "reliable agent workflows",
            "```python",
            "client = Client",
        ),
        excluded=("Pricing", "related promotional article", "Copyright Footer"),
        metadata=("title", "description", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="article",
        expected=(
            "City Launches Open Data Portal",
            "By Maya Torres",
            "weekly updates",
            "historical archives",
        ),
        excluded=("Accept all cookies", "newsletter popup", "Advertise"),
        metadata=("title", "description", "og:type", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="ecommerce",
        expected=(
            "TrailRunner Pro Backpack",
            "$129.00",
            "In stock",
            "Capacity: 32L",
        ),
        excluded=("Limited time promo", "unrelated upsell", "Account"),
        metadata=("title", "description", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="forum",
        expected=(
            "Database Migration Discussion",
            "Original post by alex",
            "sam: Take a SQLite online backup",
            "nora: Keep the old database",
        ),
        excluded=("Forum Home", "Share buttons"),
        metadata=("title", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="table",
        expected=(
            "Regional Sales Table",
            "| Region",
            "$120,000",
            "West",
        ),
        excluded=("Analytics Portal Navigation", "Download app footer"),
        metadata=("title", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="blog",
        expected=(
            "How Agents Read The Web",
            "By Irene Shaw",
            "stable context",
            "fixed fixtures",
        ),
        excluded=("Home Archive", "weekly AI newsletter"),
        metadata=("title", "description", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="canonical",
        expected=(
            "Canonical Deployment Guide",
            "backup, health checks, and rollback",
            "authenticated stats endpoint",
            "roll back to the previous deployment",
        ),
        excluded=("Billing", "Legacy footer"),
        metadata=("title", "description", "canonical", "source_url", "final_url", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="messy_docs",
        expected=(
            "Install Agent Runner",
            "isolated agent workflows",
            "```bash",
            "agent-runner doctor",
            "Configuration guide",
        ),
        excluded=("Pricing", "weekly newsletter popup", "related promotional article", "Copyright Footer"),
        metadata=("title", "description", "canonical", "source_url", "final_url", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="noisy_article",
        expected=(
            "Transit Agency Opens Realtime Feed",
            "By Lena Ortiz",
            "route identifiers",
            "stable JSON endpoints",
        ),
        excluded=("Sponsored cloud analytics", "Subscribe to our daily briefing", "Advertise with us"),
        metadata=("title", "description", "og:type", "canonical", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="complex_product",
        expected=(
            "Field Recorder Kit",
            "$249.00",
            "Ships in 2 business days",
            "| Variant",
            "128GB",
        ),
        excluded=("Customers also bought", "Download app footer", "Clearance"),
        metadata=(
            "title",
            "description",
            "canonical",
            "jsonld_count",
            "schema_types",
            "product_name",
            "product_price",
            "product_currency",
            "extraction_strategy",
            "selected_content_hint",
        ),
    ),
    QualityCase(
        name="api_reference",
        expected=(
            "Extraction API Reference",
            "```python",
            "crawler.scrape",
            "| Parameter",
            "```json",
        ),
        excluded=("Pricing", "Legacy SDK", "Hidden accessibility duplicate", "Footer legal"),
        metadata=("title", "description", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="media_article",
        expected=(
            "Researchers Publish Open Climate Archive",
            "By Noor Patel",
            "Coastal sensors reported hourly salinity changes",
            "citation-friendly source URL",
            "Stable provenance is essential",
        ),
        excluded=("Advertise", "celebrity weather myths", "Newsletter signup"),
        metadata=("title", "description", "og:type", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="nested_sidebar_docs",
        expected=(
            "Agent Session Recovery",
            "resume a crawl after a process restart",
            "```python",
            "crawler.crawl",
            "| Field",
            "Completed URLs",
        ),
        excluded=(
            "Login Pricing",
            "Sidebar upgrade promo",
            "Legacy crawler migration checklist",
            "Enterprise upgrade banner",
            "Footer documentation archive",
        ),
        metadata=("title", "description", "canonical", "extraction_strategy", "selected_content_hint"),
    ),
    QualityCase(
        name="spa_rendered",
        expected=(
            "Rendered Agent Dashboard",
            "active crawl workers",
            "| Metric",
            "Queue latency",
            "```json",
        ),
        excluded=("Upgrade to the hosted dashboard", "Enable JavaScript"),
        metadata=("title", "description", "extraction_strategy", "selected_content_hint"),
    ),
]


@pytest.mark.parametrize("case", QUALITY_CASES, ids=lambda case: case.name)
def test_quality_fixture_outputs_agent_ready_markdown(case: QualityCase) -> None:
    doc = AgentCrawl({"fetcher": "http"}).scrape(str(FIXTURE_DIR / f"{case.name}.html"))

    assert isinstance(doc, ScrapeDocument)
    assert doc.ok
    assert doc.markdown.strip()
    assert doc.text.strip()
    for text in case.expected:
        assert text in doc.markdown
    for text in case.excluded:
        assert text not in doc.markdown
    for key in case.metadata:
        assert doc.metadata.get(key)
    assert doc.metadata["markdown_chars"] == len(doc.markdown)
    assert doc.metadata["text_chars"] == len(doc.text)
    assert doc.metadata["link_count"] == len(doc.links)
    assert doc.metadata["content_format"] == "markdown"
    assert doc.metadata["final_url"] == doc.metadata["source_url"]
    assert doc.metadata["extraction_strategy"] in {
        "main_content",
        "document_passthrough",
    }
    assert isinstance(doc.metadata["selected_content_hint"], str)
    assert doc.metadata["selected_content_hint"]
    if doc.metadata["extraction_strategy"] == "main_content":
        assert doc.metadata["candidate_count"] >= 1
        assert doc.metadata["selected_content_score"] > 0
        assert doc.metadata["selected_content_tag"]
        assert doc.metadata["content_sha256"]
    assert doc.metadata["heading_count"] >= 1
    assert doc.metadata["fenced_code_block_count"] % 2 == 0


@pytest.mark.parametrize("case", QUALITY_CASES, ids=lambda case: case.name)
def test_quality_fixture_text_is_not_corrupted(case: QualityCase) -> None:
    doc = AgentCrawl({"fetcher": "http"}).scrape(str(FIXTURE_DIR / f"{case.name}.html"))

    assert isinstance(doc, ScrapeDocument)
    assert doc.ok
    non_table_text = "\n".join(line for line in doc.text.splitlines() if not line.startswith("|"))
    assert "  " not in non_table_text
    assert len(doc.text) >= 80
