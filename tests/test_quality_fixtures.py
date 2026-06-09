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
        metadata=("title", "description"),
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
        metadata=("title", "description", "og:type"),
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
        metadata=("title", "description"),
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
        metadata=("title",),
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


@pytest.mark.parametrize("case", QUALITY_CASES, ids=lambda case: case.name)
def test_quality_fixture_text_is_not_corrupted(case: QualityCase) -> None:
    doc = AgentCrawl({"fetcher": "http"}).scrape(str(FIXTURE_DIR / f"{case.name}.html"))

    assert isinstance(doc, ScrapeDocument)
    assert doc.ok
    assert "  " not in doc.text
    assert len(doc.text) >= 80
