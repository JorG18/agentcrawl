from __future__ import annotations

from typing import Any, cast

from benchmarks.quality_report import run_quality_report


def test_quality_report_summarizes_all_fixtures() -> None:
    report = run_quality_report()
    fixture_count = cast(int, report["fixture_count"])
    failed = cast(int, report["failed"])
    passed = cast(int, report["passed"])
    results = cast(list[dict[str, Any]], report["results"])

    assert fixture_count >= 7
    assert failed == 0
    assert passed == fixture_count
    names = {result["name"] for result in results}
    assert {"documentation", "article", "ecommerce", "forum", "table", "blog", "canonical"} <= names
