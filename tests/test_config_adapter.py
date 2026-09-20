"""Regression tests for ``normalize_graph_config`` timeout units.

ScrapeGraphAI / LangChain loader kwargs express ``timeout`` in **seconds**
while ``CrawlConfig`` speaks milliseconds. The adapter passed the raw value
through, so ``{"timeout": 30}`` became a 30 ms timeout — every fetch failed —
and ``{"timeout": 0.5}`` was truncated to 0 by ``int()``.
"""

from __future__ import annotations

import pytest

from agentcrawl.config import CrawlConfig
from agentcrawl.config_adapter import normalize_graph_config


@pytest.mark.parametrize(
    ("seconds", "expected_ms"),
    [(30, 30_000), (10, 10_000), (0.5, 500), (1.25, 1_250), (120, 120_000)],
)
def test_loader_kwargs_timeout_is_converted_from_seconds(seconds: float, expected_ms: int) -> None:
    normalized = normalize_graph_config({"loader_kwargs": {"timeout": seconds}})
    assert normalized["timeout_ms"] == expected_ms
    assert CrawlConfig.from_dict(normalized).timeout_ms == expected_ms


def test_explicit_timeout_ms_wins_over_loader_kwargs() -> None:
    normalized = normalize_graph_config({"timeout_ms": 45_000, "loader_kwargs": {"timeout": 3}})
    assert normalized["timeout_ms"] == 45_000


@pytest.mark.parametrize("bad", [0, -5, "abc"])
def test_invalid_loader_timeout_is_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        normalize_graph_config({"loader_kwargs": {"timeout": bad}})


def test_absent_loader_timeout_leaves_the_default_alone() -> None:
    # ``timeout: None`` means "not supplied", not "zero": it must not fail or
    # overwrite the engine default.
    normalized = normalize_graph_config({"loader_kwargs": {"timeout": None}})
    assert "timeout_ms" not in normalized
