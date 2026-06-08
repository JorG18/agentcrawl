from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agentcrawl import AgentCrawler
from agentcrawl.parsing import chunk_text, html_to_markdown


class Product(BaseModel):
    name: str
    price: str


def test_parse_and_chunk() -> None:
    markdown = html_to_markdown(
        "<html><body><h1>Hello</h1><p>World</p></body></html>", AgentCrawler().config
    )
    assert "Hello" in markdown
    assert chunk_text(markdown, AgentCrawler().config)


def test_extract_local_file_with_fake_llm(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    html.write_text("<h1>Widget</h1><p>Price: 10 EUR</p>", encoding="utf-8")

    crawler = AgentCrawler(
        {
            "llm": lambda _prompt: '{"name": "Widget", "price": "10 EUR"}',
            "fetcher": "http",
            "auto_reattempt": False,
        }
    )

    result = crawler.extract(str(html), "Extract product name and price.", Product)

    assert result.ok
    assert result.answer == {"name": "Widget", "price": "10 EUR"}


def test_main_content_removes_page_chrome_and_preserves_structure() -> None:
    html = """
    <html><body>
      <header><a href="/">Brand</a></header>
      <nav>Products Pricing Contact</nav>
      <div class="cookie-banner">Accept cookies</div>
      <main><article>
        <h1>Parsing Guide</h1>
        <p>Keep this introduction.</p>
        <ul><li>First item</li><li>Second item</li></ul>
        <table>
          <tr><th>Name</th><th>Price</th></tr>
          <tr><td>Widget</td><td>10 EUR</td></tr>
        </table>
        <pre><code>def hello():
    return "world"</code></pre>
      </article></main>
      <aside>Related articles</aside>
      <footer>Copyright</footer>
    </body></html>
    """

    markdown = html_to_markdown(html, AgentCrawler().config, only_main_content=True)

    assert "# Parsing Guide" in markdown
    assert "Keep this introduction." in markdown
    assert "First item" in markdown
    assert "Widget" in markdown and "10 EUR" in markdown
    assert "def hello():" in markdown
    assert 'return "world"' in markdown
    assert "Products Pricing Contact" not in markdown
    assert "Accept cookies" not in markdown
    assert "Related articles" not in markdown
    assert "Copyright" not in markdown


def test_full_content_keeps_header_but_removes_unsafe_content() -> None:
    html = """
    <header><p>Site introduction</p></header>
    <main><h1>Article</h1><script>bad()</script></main>
    """

    markdown = html_to_markdown(html, AgentCrawler().config, only_main_content=False)

    assert "Site introduction" in markdown
    assert "Article" in markdown
    assert "bad()" not in markdown
