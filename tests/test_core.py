from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from agentcrawl import AgentCrawler
from agentcrawl.parsing import _clean_markdown, chunk_text, html_to_markdown


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


def test_code_blocks_preserve_language_from_class_names() -> None:
    html = """
    <main>
      <h1>Code examples</h1>
      <pre><code>plain block</code></pre>
      <pre><code class="language-python">print("hello")</code></pre>
      <pre><code class="lang-javascript">console.log("hello")</code></pre>
    </main>
    """

    markdown = html_to_markdown(html, AgentCrawler().config, only_main_content=True)

    assert "plain block" in markdown
    assert "```python" in markdown
    assert 'print("hello")' in markdown
    assert "```javascript" in markdown
    assert 'console.log("hello")' in markdown


def test_clean_markdown_keeps_code_language_indexes_for_mixed_code_markers() -> None:
    markdown = "\n".join(
        [
            "[code]",
            "plain block",
            "[/code]",
            "```",
            'print("hello")',
            "```",
            "[code]",
            'console.log("hello")',
            "[/code]",
        ]
    )

    cleaned = _clean_markdown(markdown, {1: "python", 2: "javascript"})

    assert cleaned.splitlines() == [
        "```",
        "plain block",
        "```",
        "```python",
        'print("hello")',
        "```",
        "```javascript",
        'console.log("hello")',
        "```",
    ]


def test_clean_markdown_preserves_native_fence_languages_without_desync() -> None:
    markdown = "\n".join(
        [
            "```python",
            'print("native")',
            "```",
            "[code]",
            'console.log("mapped")',
            "[/code]",
        ]
    )

    cleaned = _clean_markdown(markdown, {1: "javascript"})

    assert cleaned.splitlines() == [
        "```python",
        'print("native")',
        "```",
        "```javascript",
        'console.log("mapped")',
        "```",
    ]


def test_main_content_prefers_text_rich_article_candidate_without_semantic_tags() -> None:
    html = """
    <html><body>
      <div class="topbar"><a href="/a">A</a><a href="/b">B</a><a href="/c">C</a></div>
      <div class="layout">
        <div class="sidebar related-posts">Related link farm</div>
        <div class="post-body">
          <h1>Deep Dive</h1>
          <p>This paragraph contains the useful information agents need.</p>
          <p>Another substantial paragraph keeps the content score high.</p>
        </div>
      </div>
      <div class="footer">Footer links</div>
    </body></html>
    """

    markdown = html_to_markdown(html, AgentCrawler().config, only_main_content=True)

    assert "# Deep Dive" in markdown
    assert "useful information agents need" in markdown
    assert "Another substantial paragraph" in markdown
    assert "Related link farm" not in markdown
    assert "Footer links" not in markdown


def test_tables_keep_header_separator_and_cell_values() -> None:
    html = """
    <main>
      <h1>Pricing</h1>
      <table>
        <thead><tr><th>Plan</th><th>Price</th><th>Includes</th></tr></thead>
        <tbody>
          <tr><td>Community</td><td>Free</td><td>Markdown extraction</td></tr>
          <tr><td>Enhanced</td><td>Paid</td><td>Browser workflows</td></tr>
        </tbody>
      </table>
    </main>
    """

    markdown = html_to_markdown(html, AgentCrawler().config, only_main_content=True)

    assert "| Plan" in markdown
    assert "| Community" in markdown
    assert "| Enhanced" in markdown
    assert "Markdown extraction" in markdown
    assert "Browser workflows" in markdown
