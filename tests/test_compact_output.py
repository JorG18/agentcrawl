"""Markdown stays compact: no table padding, no duplicate columns, absolute links."""

from __future__ import annotations

from agentcrawl.config import CrawlConfig
from agentcrawl.parsing import html_to_markdown

GITHUB_LIKE_TABLE = """
<table><thead><tr><th><span class="sr-only">Name</span></th><th>Name</th>
<th>Last commit message</th><th>Last commit date</th></tr></thead><tbody>
<tr><td><a href="/o/r/tree/main/docs" title="docs">docs</a></td>
<td><a href="/o/r/tree/main/docs" title="docs">docs</a></td><td></td><td></td></tr>
<tr><td><a href="/o/r/tree/main/src" title="src">src</a></td>
<td><a href="/o/r/tree/main/src" title="src">src</a></td><td></td><td></td></tr>
</tbody></table>
"""


def _md(html: str, **kwargs) -> str:
    return html_to_markdown(html, CrawlConfig(), only_main_content=False, **kwargs)


def test_tables_have_no_padding_and_no_duplicate_or_empty_columns() -> None:
    markdown = _md(GITHUB_LIKE_TABLE, base_url="https://github.com/o/r")
    assert "   " not in markdown
    assert "| [docs](https://github.com/o/r/tree/main/docs) |" in markdown
    assert markdown.count("tree/main/docs") == 1
    assert "Last commit" not in markdown  # columns with no data are dropped


def test_regular_tables_keep_every_column() -> None:
    html = (
        "<table><tr><th>Param</th><th>Type</th><th>Default</th></tr>"
        "<tr><td>timeout</td><td>int</td><td>30</td></tr>"
        "<tr><td>retries</td><td>int</td><td>2</td></tr></table>"
    )
    markdown = _md(html)
    assert "| Param | Type | Default |" in markdown
    assert "| timeout | int | 30 |" in markdown
    assert "| retries | int | 2 |" in markdown


def test_links_and_images_become_absolute_and_lose_tooltips() -> None:
    html = (
        '<main><p><a href="../guide/install.html" title="Install the package">Install</a> '
        '<img src="/logo.png" alt="logo"></p></main>'
    )
    markdown = html_to_markdown(
        html,
        CrawlConfig(include_images=True),
        base_url="https://docs.example.org/en/latest/api/index.html",
    )
    assert "[Install](https://docs.example.org/en/latest/guide/install.html)" in markdown
    assert "https://docs.example.org/logo.png" in markdown
    assert "Install the package" not in markdown


def test_link_titles_inside_code_are_left_alone() -> None:
    html = '<pre><code>[a](b "keep this")</code></pre>'
    assert "keep this" in _md(html)


def test_local_documents_keep_relative_links() -> None:
    markdown = _md('<p><a href="other.html">other</a></p>', base_url="/tmp/docs/index.html")
    assert "[other](other.html)" in markdown
