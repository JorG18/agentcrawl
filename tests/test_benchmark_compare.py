"""Pins the public benchmark's scoring so the numbers stay comparable over time."""

from __future__ import annotations

import pytest

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


def test_draw_filters_are_mechanical(monkeypatch):
    from benchmarks import draw_neutral

    assert (
        draw_neutral.visible_text(
            "<html><head><title>t</title><style>x{}</style></head><body><p>Hi <b>there</b></p>"
            "<script>var a</script></body></html>"
        )
        == "Hi there"
    )
    monkeypatch.setattr(draw_neutral, "_robots_allow", lambda url: True)
    long_page = "<html><body><p>" + "word " * 400 + "</p></body></html>"
    monkeypatch.setattr(draw_neutral, "fetch", lambda url: (long_page.encode(), url, 200))
    known, per_domain = {"https://a.test/known"}, {"full.test": 3}
    assert draw_neutral.why_not({"url": "https://a.test/", "kind": "homepage"}, known, per_domain)
    assert draw_neutral.why_not({"url": "https://a.test/x.pdf", "kind": "inner"}, known, per_domain)
    assert draw_neutral.why_not({"url": "https://a.test/known", "kind": "inner"}, known, per_domain)
    assert draw_neutral.why_not({"url": "https://full.test/p", "kind": "inner"}, known, per_domain)
    assert (
        draw_neutral.why_not({"url": "https://a.test/p", "kind": "inner"}, known, per_domain)
        is None
    )
    monkeypatch.setattr(
        draw_neutral, "fetch", lambda url: (b"<html><body>short</body></html>", url, 200)
    )
    assert (
        draw_neutral.why_not({"url": "https://a.test/p", "kind": "inner"}, known, per_domain)
        == "thin"
    )


def test_changed_snapshot_is_left_out(tmp_path) -> None:
    import hashlib
    import json

    from benchmarks.compare import corpus_pages

    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "same.html").write_bytes(b"<p>signal here</p>")
    (tmp_path / "snapshots" / "moved.html").write_bytes(b"<p>other bytes now</p>")
    written_from = hashlib.sha256(b"<p>signal here</p>").hexdigest()
    page = {"url": "https://x.test/", "expected": ["signal here"], "snapshot_sha256": written_from}
    manifest = tmp_path / "neutral.json"
    manifest.write_text(
        json.dumps({"pages": [{"id": "same", **page}, {"id": "moved", **page}]}), "utf-8"
    )

    pages, unscored = corpus_pages(manifest)

    assert [p.name for p in pages] == ["same"]
    assert unscored["moved"] == "snapshot changed: moved"


def test_real_tokens_use_o200k() -> None:
    tiktoken = pytest.importorskip("tiktoken")
    from benchmarks.compare import count_real_tokens

    expected = len(tiktoken.get_encoding("o200k_base").encode("hello world"))
    assert count_real_tokens("hello world") == expected


def test_missing_tiktoken_says_na(monkeypatch) -> None:
    import builtins

    from benchmarks import compare

    real_import = builtins.__import__

    def no_tiktoken(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("no tiktoken")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_tiktoken)
    compare._encoder.cache_clear()
    try:
        assert compare.count_real_tokens("hello") is None
        row = {"tool": "t", "fixtures": 1, "text_recall": 1.0, "structure_recall": None,
               "fence_language": None, "noise_leakage": 0.0, "clean_passes": 1,
               "total_tokens": 2, "total_real_tokens": None, "median_latency_ms": 1.0,
               "errors": 0}  # fmt: skip
        table = compare._markdown_table([row])
        assert "tokens (o200k)" in table and "| n/a |" in table
    finally:
        compare._encoder.cache_clear()


def test_draw_uses_the_sampler_domain_filter() -> None:
    from benchmarks import draw_neutral

    assert draw_neutral.why_not(
        {"url": "https://www.redtube.com/1", "kind": "inner"}, set(), {}
    ) == ("adult or not a site")
