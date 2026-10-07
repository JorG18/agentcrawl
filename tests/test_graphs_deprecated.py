from __future__ import annotations

import subprocess
import sys

import pytest

from agentcrawl import ExtractionGraph, MultiExtractionGraph, SearchGraph


@pytest.mark.parametrize(
    "make",
    [
        lambda: ExtractionGraph("p", "https://e.x/"),
        lambda: MultiExtractionGraph("p", ["https://e.x/"]),
        lambda: SearchGraph("p"),
    ],
)
def test_graph_classes_warn(make) -> None:
    with pytest.warns(DeprecationWarning, match="removed in 0.6"):
        make()


def test_import_does_not_warn() -> None:
    result = subprocess.run(
        [sys.executable, "-W", "always", "-c", "import agentcrawl"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "removed in 0.6" not in result.stderr
