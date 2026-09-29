"""Reading a long page in parts: outline, one section, a token cap, one fetch."""

from __future__ import annotations

from agentcrawl import AgentCrawl
from agentcrawl import crawler as crawler_module
from agentcrawl.chunks import outline_markdown, select_section

MD = (
    "Intro\n\n# Guide\n\nA\n\n## Install\n\npip install x\n\n### Linux\n\napt\n\n## Usage\n\nrun it"
)
PAGE = (
    "<html><body><main><h1>Guide<a class='headerlink' href='#g'>¶</a></h1><p>"
    + "Intro words. " * 40
    + "</p><h2>Install</h2><p>pip install x</p><h2>Usage</h2><p>"
    + "run it. " * 400
    + "</p></main></body></html>"
)


def test_outline_lists_sections_with_the_size_section_returns() -> None:
    outline = outline_markdown(MD)
    assert [o["id"] for o in outline] == ["s1", "s2", "s3", "s4", "s5"]
    assert outline[2]["heading"] == "Guide > Install"
    install = select_section(MD, "s3")
    assert install == "## Install\n\npip install x\n\n### Linux\n\napt"  # subsections included
    assert select_section(MD, "usage") == "## Usage\n\nrun it"
    assert select_section(MD, "nothing") is None


def _fake_fetch(monkeypatch) -> list[str]:
    calls: list[str] = []

    def fetch(source, config):
        calls.append(source)
        return PAGE, {"fetcher": "http", "final_url": source}

    monkeypatch.setattr(crawler_module, "fetch_source", fetch)
    crawler_module._FETCH_CACHE.clear()
    return calls


def test_outline_then_section_fetches_once(monkeypatch) -> None:
    calls = _fake_fetch(monkeypatch)
    crawler = AgentCrawl()
    outline = crawler.scrape("https://example.com/", formats=["outline"], max_age=60)["outline"]
    assert [o["heading"] for o in outline][-1] == "Guide > Usage"  # no "¶"
    usage = crawler.scrape(
        "https://example.com/", formats=["markdown", "metadata"], section="Usage", max_age=60
    )
    assert usage["markdown"].startswith("## Usage")
    assert "pip install" not in usage["markdown"]
    assert usage["metadata"]["cache_hit"] is True
    assert calls == ["https://example.com/"]
    # Without max_age every call fetches.
    crawler.scrape("https://example.com/", formats=["markdown"])
    assert len(calls) == 2


def test_missing_section_lists_the_ones_that_exist(monkeypatch) -> None:
    _fake_fetch(monkeypatch)
    result = AgentCrawl().scrape("https://example.com/", formats=["metadata"], section="Pricing")
    assert result["metadata"]["error_type"] == "section_not_found"
    assert [s["heading"] for s in result["metadata"]["sections"]][-1] == "Guide > Usage"


def test_max_tokens_caps_the_markdown(monkeypatch) -> None:
    _fake_fetch(monkeypatch)
    result = AgentCrawl().scrape(
        "https://example.com/", formats=["markdown", "metadata"], max_tokens=100
    )
    assert len(result["markdown"]) <= 400
    assert result["metadata"]["markdown_truncated"] is True
