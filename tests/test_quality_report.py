from __future__ import annotations

from typing import Any, cast

from benchmarks.quality_report import run_quality_report


def test_quality_report_summarizes_all_fixtures() -> None:
    report = run_quality_report()
    fixture_count = cast(int, report["fixture_count"])
    failed = cast(int, report["failed"])
    passed = cast(int, report["passed"])
    minimum_score = cast(int, report["minimum_score"])
    average_score = cast(float, report["average_score"])
    results = cast(list[dict[str, Any]], report["results"])

    assert fixture_count >= 10
    assert failed == 0
    assert passed == fixture_count
    assert minimum_score >= 85
    assert average_score >= 90
    names = {result["name"] for result in results}
    assert {
        "documentation",
        "article",
        "ecommerce",
        "forum",
        "table",
        "blog",
        "canonical",
        "messy_docs",
        "noisy_article",
        "complex_product",
        "api_reference",
        "media_article",
        "nested_sidebar_docs",
        "spa_rendered",
    } <= names
    for result in results:
        assert result["score"] >= minimum_score
        assert result["quality_checks"]["expected_content"]
        assert result["quality_checks"]["metadata"]
        assert result["quality_checks"]["boilerplate_removed"]
        assert result["quality_checks"]["provenance"]
