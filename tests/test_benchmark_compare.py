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


def test_neutral_corpus_snapshot_and_scoring(tmp_path, capsys) -> None:
    import json

    from benchmarks.snapshot import snapshot

    manifest = tmp_path / "neutral.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "pages": [
                    {
                        "id": "doc",
                        "url": "https://docs.example.com/a",
                        "expected": ["Install Guide", "Run the installer"],
                        "excluded": ["Subscribe now"],
                    },
                    {"id": "unreviewed", "url": "https://docs.example.com/b", "expected": []},
                    {"id": "down", "url": "https://down.example.com/", "expected": ["x"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    html = (
        b"<html><body><nav>Subscribe now</nav><main><h1>Install Guide</h1>"
        b"<p>Run the installer.</p></main></body></html>"
    )

    def fake_fetch(url: str):
        if "down" in url:
            raise OSError("unreachable")
        return html, url, 200

    index = snapshot(manifest, fetcher=fake_fetch)

    assert index["doc"]["bytes"] == len(html) and len(index["doc"]["sha256"]) == 64
    assert "unreachable" in index["down"]["error"]
    assert main(["--tools", "agentcrawl", "--corpus", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "| agentcrawl |" in out
    assert "unscored unreviewed: no reviewed expected signals yet" in out
    assert "unscored down: no snapshot" in out
    assert index["doc"]["sha256"] in out
