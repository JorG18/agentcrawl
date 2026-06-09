from dataclasses import asdict
from pathlib import Path
import sys
import types

import pytest

from agentcrawl import AgentCrawl, ScrapeDocument
from agentcrawl.config import CrawlConfig
import agentcrawl.crawler as crawler_module
from agentcrawl.crawler import _markdown_to_text, _read_sitemap
from agentcrawl.exceptions import FetchError
from agentcrawl.html_tools import normalize_url, same_domain


def test_scrape_local_html_returns_document(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    about = tmp_path / "about.html"
    about.write_text("<title>About</title><h1>About</h1>", encoding="utf-8")
    page.write_text(
        """
        <html>
          <head><title>Home</title><meta name="description" content="A test page"></head>
          <body><main><h1>Hello</h1><a href="about.html">About</a></main></body>
        </html>
        """,
        encoding="utf-8",
    )

    crawler = AgentCrawl({"fetcher": "http"})
    doc = crawler.scrape(str(page))

    assert isinstance(doc, ScrapeDocument)
    assert doc.ok
    assert "Hello" in doc.markdown
    assert doc.metadata["title"] == "Home"
    assert str(about) in doc.links


def test_scrape_formats(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text("<h1>Hello</h1><p>World</p>", encoding="utf-8")

    crawler = AgentCrawl({"fetcher": "http"})
    payload = crawler.scrape(str(page), formats=["markdown", "text", "metadata"])

    assert isinstance(payload, dict)
    assert "Hello" in payload["markdown"]
    assert "World" in payload["text"]


def test_scrape_returns_document_error_for_expected_fetch_failures(monkeypatch) -> None:
    def fake_fetch_source(_source, _config):
        raise FetchError("network unavailable")

    monkeypatch.setattr(crawler_module, "fetch_source", fake_fetch_source)

    doc = AgentCrawl().scrape("https://example.com")

    assert isinstance(doc, ScrapeDocument)
    assert doc.errors == ["network unavailable"]
    assert doc.metadata["error_type"] == "fetch_error"


def test_scrape_does_not_swallow_internal_bugs(monkeypatch) -> None:
    def broken_extract_html_facts(_html, _source):
        raise TypeError("internal parser bug")

    monkeypatch.setattr(crawler_module, "fetch_source", lambda _source, _config: ("<html></html>", {}))
    monkeypatch.setattr(crawler_module, "extract_html_facts", broken_extract_html_facts)

    with pytest.raises(TypeError, match="internal parser bug"):
        AgentCrawl().scrape("https://example.com")


def test_markdown_to_text_preserves_legitimate_line_prefixes() -> None:
    markdown = "\n".join(
        [
            "# Heading",
            "1.5 GB RAM",
            "2024-06-08 release",
            "#10 ranking",
            "...y continúa",
            "- Bullet item",
            "1. Ordered item",
            "> Quoted item",
        ]
    )

    assert _markdown_to_text(markdown).splitlines() == [
        "Heading",
        "1.5 GB RAM",
        "2024-06-08 release",
        "#10 ranking",
        "...y continúa",
        "Bullet item",
        "Ordered item",
        "Quoted item",
    ]


def test_read_sitemap_expands_sitemap_index(tmp_path: Path, monkeypatch) -> None:
    import agentcrawl.crawler as crawler_module

    sitemap_index = "https://example.com/sitemap.xml"
    nested_sitemap = "https://example.com/post-sitemap.xml"
    xml_by_url = {
        sitemap_index: """
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://example.com/post-sitemap.xml</loc></sitemap>
            </sitemapindex>
        """,
        nested_sitemap: """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/a</loc></url>
              <url><loc>https://example.com/b?utm_source=ignored</loc></url>
            </urlset>
        """,
    }

    class FakeResponse:
        def __init__(self, url: str):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self) -> str:
            return self.url

        def read(self) -> bytes:
            return xml_by_url[self.url].encode("utf-8")

    def fake_urlopen(request, timeout):
        return FakeResponse(request.full_url)

    monkeypatch.setattr(crawler_module.urllib.request, "urlopen", fake_urlopen)

    assert _read_sitemap(sitemap_index, CrawlConfig()) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_map_discovers_sitemap_declared_in_robots_txt(monkeypatch) -> None:
    import agentcrawl.crawler as crawler_module

    root = "https://example.com/"
    robots_url = "https://example.com/robots.txt"
    sitemap_url = "https://cdn.example.com/custom-sitemap.xml"
    seen_requests: list[str] = []

    class FakeResponse:
        def __init__(self, url: str):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self) -> str:
            return self.url

        def read(self) -> bytes:
            if self.url == robots_url:
                return f"User-agent: *\nAllow: /\nSitemap: {sitemap_url}\n".encode()
            raise AssertionError(f"unexpected urlopen for {self.url}")

    def fake_urlopen(request, timeout):
        seen_requests.append(request.full_url)
        return FakeResponse(request.full_url)

    monkeypatch.setattr(crawler_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(crawler_module, "_read_sitemap", lambda _url, _config: [f"{root}from-sitemap"])
    monkeypatch.setattr(AgentCrawl, "scrape", lambda self, source: ScrapeDocument(url=source, markdown="", text=""))

    result = AgentCrawl({"respect_robots_txt": False}).map(root)

    assert robots_url in seen_requests
    assert result.urls == ["https://example.com/from-sitemap"]


def test_scrape_local_markdown_text_json_and_xml_documents(tmp_path: Path) -> None:
    markdown_file = tmp_path / "notes.md"
    text_file = tmp_path / "notes.txt"
    json_file = tmp_path / "data.json"
    xml_file = tmp_path / "feed.xml"
    markdown_file.write_text("# Notes\n\nKeep this markdown.", encoding="utf-8")
    text_file.write_text("Plain document text.", encoding="utf-8")
    json_file.write_text('{"name":"AgentCrawl","ok":true}', encoding="utf-8")
    xml_file.write_text("<root><item>Value</item></root>", encoding="utf-8")

    crawler = AgentCrawl({"fetcher": "http"})
    markdown_doc = crawler.scrape(str(markdown_file))
    text_doc = crawler.scrape(str(text_file))
    json_doc = crawler.scrape(str(json_file))
    xml_doc = crawler.scrape(str(xml_file))

    assert isinstance(markdown_doc, ScrapeDocument)
    assert markdown_doc.markdown == "# Notes\n\nKeep this markdown."
    assert markdown_doc.metadata["document_type"] == "markdown"
    assert isinstance(text_doc, ScrapeDocument)
    assert text_doc.markdown == "Plain document text."
    assert isinstance(json_doc, ScrapeDocument)
    assert "```json" in json_doc.markdown
    assert '"name": "AgentCrawl"' in json_doc.markdown
    assert isinstance(xml_doc, ScrapeDocument)
    assert "```xml" in xml_doc.markdown
    assert "<item>Value</item>" in xml_doc.markdown


def test_scrape_local_pdf_with_optional_pymupdf_backend(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def get_text(self, _format: str) -> str:
            return self.text

    class FakeDocument:
        metadata = {"title": "Sample PDF"}
        page_count = 2

        def __iter__(self):
            return iter([FakePage("First page text"), FakePage("Second page text")])

        def close(self) -> None:
            pass

    fake_fitz = types.SimpleNamespace(open=lambda _path: FakeDocument())
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

    doc = AgentCrawl({"fetcher": "http"}).scrape(str(pdf))

    assert isinstance(doc, ScrapeDocument)
    assert doc.ok
    assert "## Page 1" in doc.markdown
    assert "First page text" in doc.markdown
    assert "## Page 2" in doc.markdown
    assert doc.metadata["document_type"] == "pdf"
    assert doc.metadata["page_count"] == 2


def test_crawl_local_html_same_directory(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    page2 = tmp_path / "page2.html"
    page2.write_text("<h1>Second</h1>", encoding="utf-8")
    index.write_text('<h1>First</h1><a href="page2.html">Second</a>', encoding="utf-8")

    crawler = AgentCrawl({"fetcher": "http", "crawl_same_domain": False, "crawl_depth": 1})
    run = crawler.crawl(str(index), max_pages=2)

    assert len(run.documents) == 2
    assert any("Second" in doc.markdown for doc in run.documents)


def test_scrape_defaults_to_main_content_and_can_return_full_page(tmp_path: Path) -> None:
    page = tmp_path / "main-content.html"
    page.write_text(
        """
        <header><p>Global navigation</p></header>
        <main><h1>Primary article</h1><p>Useful body.</p></main>
        """,
        encoding="utf-8",
    )
    crawler = AgentCrawl({"fetcher": "http"})

    main = crawler.scrape(str(page))
    full = crawler.scrape(str(page), only_main_content=False)

    assert isinstance(main, ScrapeDocument)
    assert isinstance(full, ScrapeDocument)
    assert "Primary article" in main.markdown
    assert "Global navigation" not in main.markdown
    assert main.metadata["only_main_content"] is True
    assert "Global navigation" in full.markdown
    assert full.metadata["only_main_content"] is False


def test_crawl_reports_progress_and_supports_cancellation(tmp_path: Path) -> None:
    index = tmp_path / "cancel.html"
    second = tmp_path / "second.html"
    second.write_text("<h1>Second</h1>", encoding="utf-8")
    index.write_text('<h1>First</h1><a href="second.html">Second</a>', encoding="utf-8")
    crawler = AgentCrawl({"fetcher": "http", "crawl_same_domain": False})
    reports: list[dict[str, int | bool]] = []

    run = crawler.crawl(
        str(index),
        max_pages=2,
        progress_callback=reports.append,
        should_cancel=lambda: len(reports) >= 2,
    )

    assert run.metadata["cancelled"] is True
    assert run.metadata["visited"] == 1
    assert run.metadata["pending"] == 1
    assert reports[-1]["cancelled"] is True


def test_crawl_respects_robots_policy(tmp_path: Path, monkeypatch) -> None:
    import agentcrawl.crawler as crawler_module

    class DenyAll:
        def can_fetch(self, _agent: str, _url: str) -> bool:
            return False

    page = tmp_path / "blocked.html"
    page.write_text("<h1>Blocked</h1>", encoding="utf-8")
    monkeypatch.setattr(crawler_module, "_load_robots", lambda _url, _config: DenyAll())
    crawler = AgentCrawl({"fetcher": "http", "respect_robots_txt": True})

    run = crawler.crawl(str(page), max_pages=1)

    assert not run.documents
    assert run.metadata["failed"] == 1
    assert "blocked by robots.txt" in run.errors[0]


def test_url_canonicalization_removes_tracking_and_normalizes_origin() -> None:
    first = normalize_url(
        "HTTPS://Example.COM:443/docs?b=2&utm_source=test&a=1#section",
        "https://example.com/",
    )
    second = normalize_url(
        "https://example.com/docs?a=1&b=2&fbclid=ignored",
        "https://example.com/",
    )

    assert first == "https://example.com/docs?a=1&b=2"
    assert second == first
    assert same_domain(first, "https://EXAMPLE.com:443/")
    assert not same_domain(first, "http://example.com/")


def test_unicode_domains_normalize_to_idna_for_same_domain() -> None:
    unicode_url = normalize_url("https://bücher.example/path", "https://bücher.example/")
    punycode_url = normalize_url("https://xn--bcher-kva.example/path", "https://xn--bcher-kva.example/")

    assert unicode_url == "https://xn--bcher-kva.example/path"
    assert same_domain(unicode_url, punycode_url)
    assert same_domain("https://bücher.example/a", "https://xn--bcher-kva.example/b")


def test_crawl_deduplicates_canonical_url_variants(monkeypatch) -> None:
    root = "https://example.com/"
    target = "https://example.com/page?a=1&b=2"
    calls: list[str] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        calls.append(source)
        links = []
        if source == root:
            links = [
                "https://EXAMPLE.com:443/page?b=2&a=1&utm_medium=test#top",
                "https://example.com/page?a=1&b=2&fbclid=ignored",
                target,
            ]
        return ScrapeDocument(url=source, markdown="# Page", text="Page", links=links)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    crawler = AgentCrawl({"respect_robots_txt": False, "crawl_depth": 1})

    run = crawler.crawl(root, max_pages=10)

    assert calls == [root, target]
    assert run.visited_urls == [root, target]
    assert run.metadata["discovered"] == 2


def test_crawl_resumes_from_checkpoint_without_repeating_visited_pages(monkeypatch) -> None:
    root = "https://example.com/"
    first = "https://example.com/first"
    second = "https://example.com/second"
    calls: list[str] = []
    checkpoints: list[dict] = []
    persisted_documents: list[dict] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        calls.append(source)
        links = [first, second] if source == root else []
        return ScrapeDocument(url=source, markdown=f"# {source}", text=source, links=links)

    def interrupt_after_root(checkpoint, progress, document):
        checkpoints.append(checkpoint)
        if document is not None:
            persisted_documents.append(
                {
                    "url": document.url,
                    "markdown": document.markdown,
                    "text": document.text,
                    "html": document.html,
                    "links": document.links,
                    "metadata": document.metadata,
                    "errors": document.errors,
                }
            )
        if progress["visited"] == 1:
            raise RuntimeError("simulated process stop")

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    crawler = AgentCrawl({"respect_robots_txt": False, "crawl_depth": 1})

    try:
        crawler.crawl(root, checkpoint_callback=interrupt_after_root)
    except RuntimeError as exc:
        assert str(exc) == "simulated process stop"

    checkpoint = checkpoints[-1]
    assert checkpoint["visited"] == [root]
    checkpoint["documents"] = persisted_documents
    calls.clear()

    resumed = crawler.crawl(root, resume_state=checkpoint)

    assert calls == [first, second]
    assert resumed.visited_urls == [root, first, second]
    assert len(resumed.documents) == 3


def test_async_crawl_checkpoints_transient_retry_without_waiting(monkeypatch) -> None:
    url = "https://example.com/unstable"
    calls: list[str] = []
    checkpoints: list[dict] = []

    def failing_scrape(self, source, formats=None, only_main_content=None):
        calls.append(source)
        return ScrapeDocument(
            url=source,
            markdown="",
            text="",
            errors=["HTTP fetch failed: HTTP Error 503"],
        )

    monkeypatch.setattr(AgentCrawl, "scrape", failing_scrape)
    crawler = AgentCrawl(
        {
            "respect_robots_txt": False,
            "crawl_url_retries": 2,
            "crawl_retry_delay": 30.0,
        }
    )

    run = crawler.crawl(
        url,
        max_pages=1,
        checkpoint_callback=lambda checkpoint, progress, document: checkpoints.append(checkpoint),
    )

    assert calls == [url]
    assert run.documents == []
    assert run.metadata["retry_scheduled"] is True
    assert run.metadata["pending"] == 1
    assert run.metadata["retries"] == 1
    assert checkpoints[-1]["queue"][0]["attempt"] == 1
    assert checkpoints[-1]["queue"][0]["ready_at"] > 0


def test_crawl_resumes_retry_and_succeeds(monkeypatch) -> None:
    url = "https://example.com/unstable"
    attempts = 0
    checkpoint: dict = {}

    def flaky_scrape(self, source, formats=None, only_main_content=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ScrapeDocument(url=source, markdown="", text="", errors=["timeout"])
        return ScrapeDocument(url=source, markdown="# Recovered", text="Recovered")

    def save_checkpoint(state, progress, document):
        checkpoint.clear()
        checkpoint.update(state)

    monkeypatch.setattr(AgentCrawl, "scrape", flaky_scrape)
    crawler = AgentCrawl(
        {
            "respect_robots_txt": False,
            "crawl_url_retries": 2,
            "crawl_retry_delay": 30.0,
        }
    )
    first = crawler.crawl(url, max_pages=1, checkpoint_callback=save_checkpoint)
    assert first.metadata["retry_scheduled"] is True
    checkpoint["queue"][0]["ready_at"] = 0

    resumed = crawler.crawl(url, max_pages=1, resume_state=checkpoint)

    assert attempts == 2
    assert resumed.metadata["retry_scheduled"] is False
    assert resumed.metadata["visited"] == 1
    assert resumed.documents[0].markdown == "# Recovered"


def test_crawl_does_not_retry_permanent_not_found(monkeypatch) -> None:
    url = "https://example.com/missing"
    calls = 0

    def missing_scrape(self, source, formats=None, only_main_content=None):
        nonlocal calls
        calls += 1
        return ScrapeDocument(
            url=source,
            markdown="",
            text="",
            errors=["HTTP fetch failed: HTTP Error 404"],
        )

    monkeypatch.setattr(AgentCrawl, "scrape", missing_scrape)
    crawler = AgentCrawl({"respect_robots_txt": False, "crawl_url_retries": 3})

    run = crawler.crawl(url, max_pages=1)

    assert calls == 1
    assert run.metadata["retry_scheduled"] is False
    assert run.metadata["failed"] == 1
    assert run.metadata["terminal_failures"] == [
        {
            "url": url,
            "attempts": 1,
            "error_type": "not_found",
            "message": "HTTP fetch failed: HTTP Error 404",
            "retryable": False,
            "failed_at": run.metadata["terminal_failures"][0]["failed_at"],
        }
    ]


def test_crawl_page_quantum_yields_and_resumes(monkeypatch) -> None:
    root = "https://example.com/"
    child = "https://example.com/child"
    checkpoints: list[dict] = []

    def fake_scrape(self, source, formats=None, only_main_content=None):
        links = [child] if source == root else []
        return ScrapeDocument(url=source, markdown=f"# {source}", text=source, links=links)

    monkeypatch.setattr(AgentCrawl, "scrape", fake_scrape)
    crawler = AgentCrawl({"respect_robots_txt": False, "crawl_depth": 1})
    first = crawler.crawl(
        root,
        max_pages=2,
        max_run_pages=1,
        checkpoint_callback=lambda checkpoint, _progress, _document: checkpoints.append(checkpoint),
    )

    assert first.metadata["fairness_yielded"] is True
    assert first.visited_urls == [root]
    resume_state = {**checkpoints[-1], "documents": [asdict(first.documents[0])]}
    resumed = crawler.crawl(root, max_pages=2, resume_state=resume_state, max_run_pages=1)

    assert resumed.metadata["fairness_yielded"] is False
    assert resumed.visited_urls == [root, child]
