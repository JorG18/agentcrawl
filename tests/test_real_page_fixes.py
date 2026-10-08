"""Extraction defects the neutral corpus found on real pages (0.4.5)."""

from __future__ import annotations

from agentcrawl.config import CrawlConfig
from agentcrawl.parsing import html_to_markdown


def _md(html: str) -> str:
    return html_to_markdown(html, CrawlConfig(), only_main_content=True)


def _page(body: str) -> str:
    return f"<html><head><title>T</title></head><body>{body}</body></html>"


def test_inline_code_does_not_shift_fence_languages() -> None:
    # The Rust book: inline `rustup` before the first block moved every
    # language one fence down.
    html = _page(
        "<main><h1>Install</h1><p>Use <code>rustup</code> to install Rust on your machine.</p>"
        '<pre><code class="language-console">$ rustc --version</code></pre>'
        "<p>Then write <code>main.rs</code> with the program below.</p>"
        '<pre><code class="language-rust">fn main() {}</code></pre></main>'
    )
    markdown = _md(html)
    assert "```console\n$ rustc --version\n```" in markdown
    assert "```rust\nfn main() {}\n```" in markdown


def test_sphinx_and_mdn_languages_are_read() -> None:
    html = _page(
        '<div role="main"><h1>Docs</h1><p>Example text long enough to be content here.</p>'
        '<div class="highlight-python3 notranslate"><div class="highlight"><pre>x = 1</pre></div></div>'
        '<pre class="brush: js notranslate">fetch(url)</pre>'
        '<div class="highlight-default notranslate"><div class="highlight"><pre>$ ls</pre></div></div>'
        "</div>"
    )
    markdown = _md(html)
    assert "```python\nx = 1\n```" in markdown
    assert "```js\nfetch(url)\n```" in markdown
    assert "```\n$ ls\n```" in markdown


def test_code_blocks_are_not_indented_inside_fences() -> None:
    html = _page(
        "<article><h1>Code</h1><p>Some explanation of the code sample below.</p>"
        "<pre>def f():\n    return 1</pre></article>"
    )
    assert "```\ndef f():\n    return 1\n```" in _md(html)


def test_no_space_between_emphasis_and_punctuation() -> None:
    html = _page(
        "<article><p>A <b>web crawler</b>, sometimes called a <b>spider</b>. "
        "<i>Freshness</i>: a binary measure (<b>bold</b>).</p><p>More text here.</p></article>"
    )
    markdown = _md(html)
    assert "**web crawler**, sometimes" in markdown
    assert "_Freshness_:" in markdown
    assert "**spider**." in markdown


def test_rfc_appendices_are_kept_and_only_the_index_is_dropped() -> None:
    html = _page(
        "<main><h1>RFC 9999</h1><section id='section-1'><h2>1. Introduction</h2>"
        "<p>The protocol body text that matters for implementers.</p><p>More.</p></section>"
        "<section id='appendix-C'><h2>Acknowledgements</h2><p>Thanks to the working group.</p></section>"
        "<section id='appendix-D'><h2>Index</h2><ul><li><a href='#a'>A</a></li>"
        "<li><a href='#b'>B</a></li></ul></section>"
        "<section id='appendix-E'><h2>Authors' Addresses</h2><p>Roy T. Fielding (editor)</p></section>"
        "</main>"
    )
    markdown = _md(html)
    assert "Thanks to the working group." in markdown
    assert "Roy T. Fielding (editor)" in markdown
    assert "## Index" not in markdown


def test_screen_reader_table_header_is_kept() -> None:
    html = _page(
        "<article><h1>Endpoint</h1><p>Parameters accepted by this endpoint are below.</p>"
        '<table><thead class="visually-hidden"><tr><th>Name, Type, Description</th></tr></thead>'
        "<tbody><tr><td>accept string</td></tr><tr><td>org string</td></tr></tbody></table>"
        '<p class="sr-only">Screen reader only note</p></article>'
    )
    markdown = _md(html)
    assert "Name, Type, Description" in markdown
    assert "Screen reader only note" not in markdown


def test_main_landmark_beats_the_body_around_it() -> None:
    sidebar = "".join(f"<p>Previous topic item {i} with some words</p>" for i in range(12))
    article = "".join(
        f"<p>Real paragraph {i} about JSON decoding and errors.</p>" for i in range(30)
    )
    html = _page(
        "<a href='#main'>Jump to content</a>"
        f'<div class="document"><div class="body" role="main"><h1>json</h1>{article}</div></div>'
        f'<div class="sphinxsidebarwrapper">{sidebar}<p>Report a bug</p></div>'
    )
    markdown = _md(html)
    assert "Real paragraph 29" in markdown
    assert "Report a bug" not in markdown
    assert "Jump to content" not in markdown


# Found by the web-sample benchmark (random real homepages).


def test_layout_class_containing_index_is_not_a_generated_index() -> None:
    # estama.jp: "index-column_main" held the whole page and was dropped.
    items = "".join(f"<li>Shop number {i} with a description of the place</li>" for i in range(20))
    html = _page(
        '<main><div class="flex-column2"><div class="index-column_main">'
        f"<h2>Featured</h2><ul>{items}</ul></div></div></main>"
    )
    assert "Shop number 19" in _md(html)


def test_index_class_on_the_page_wrapper_is_not_a_generated_index() -> None:
    # biyiyd.cc, henglin.com: <div class="main index"> held the whole homepage.
    items = "".join(
        f"<p>Novel number {i} with a short summary of the story.</p>" for i in range(12)
    )
    assert "Novel number 11" in _md(_page(f'<div class="main index"><h2>New</h2>{items}</div>'))


def test_generated_index_is_still_dropped() -> None:
    article = "".join(f"<p>Section {i} of the reference explains a feature.</p>" for i in range(8))
    terms = "".join(f"<li>term{i}, 1</li>" for i in range(30))
    markdown = _md(_page(f"<main>{article}<div><h2>Index</h2><ul>{terms}</ul></div></main>"))
    assert "Section 7" in markdown and "term29" not in markdown


def test_a_modal_is_never_selected_as_main_content() -> None:
    offers = "".join(f"<p>Used car offer {i} with price, mileage and year.</p>" for i in range(12))
    html = _page(
        f'<div class="listing"><h1>Cars</h1>{offers}</div>'
        '<div class="modal-content"><p>Sign in to save cars and get alerts.</p>'
        "<p>Enter your phone number to continue with your account.</p>"
        "<p>We will send you a code by message right away today.</p></div>"
    )
    markdown = _md(html)
    assert "Used car offer 11" in markdown


def test_content_hidden_until_scripts_run_is_not_thrown_away() -> None:
    # life360.com: the whole page sits in a Tailwind "hidden" wrapper that
    # its scripts reveal; returning nothing was worse than returning it.
    body = "".join(
        f"<p>Paragraph {i} explaining how family location sharing works.</p>" for i in range(15)
    )
    html = _page(f'<main><div class="Layout_root hidden"><h2>Location</h2>{body}</div></main>')
    assert "Paragraph 14" in _md(html)


def test_small_hidden_panels_stay_hidden() -> None:
    article = "".join(
        f"<p>Article paragraph {i} with real reporting and detail.</p>" for i in range(15)
    )
    html = _page(f'<article><h1>News</h1>{article}<div class="hidden">Secret menu</div></article>')
    markdown = _md(html)
    assert "Article paragraph 14" in markdown
    assert "Secret menu" not in markdown
