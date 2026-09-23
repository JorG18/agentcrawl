"""Pins the public benchmark's scoring so the numbers stay comparable over time."""

from __future__ import annotations

from benchmarks.compare import ADAPTERS, main, score, summarize


def test_structure_is_style_neutral_and_fence_language_is_separate() -> None:
    # "table" expects "| Region"; "api_reference" expects ```python / ```json fences.
    table = score("t", "table", "Regional Sales Table\n\nRegion| Revenue\n---|---\n$120,000| x", 1)
    assert table.structure_recall == 1.0
    fences = score(
        "t",
        "api_reference",
        "Extraction API Reference\n```\ncode\n```\n| Parameter | x |",
        1,
    )
    assert fences.structure_recall == 1.0
    assert fences.fence_language == 0.0


def test_leakage_counts_known_boilerplate() -> None:
    row = score("t", "cookie_consent", "Authenticating With API Keys\nwe use cookies", 1)
    assert 0 < row.leakage < 1
    assert "we use cookies" in row.leaked


def test_agentcrawl_adapter_runs_offline(capsys) -> None:
    assert "agentcrawl" in ADAPTERS
    assert main(["--tools", "agentcrawl"]) == 0
    out = capsys.readouterr().out
    assert "| agentcrawl |" in out and "environment:" in out


def test_summary_aggregates_per_tool() -> None:
    rows = [
        score("a", "table", "Regional Sales Table | Region | $120,000", 1),
        score("a", "blog", "How Agents Read The Web By Irene Shaw fixed fixtures", 3),
    ]
    (summary,) = summarize(rows)
    assert summary["tool"] == "a" and summary["fixtures"] == 2
    assert summary["median_latency_ms"] == 2
