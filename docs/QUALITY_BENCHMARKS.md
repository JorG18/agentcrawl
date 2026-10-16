# Quality Benchmarks

Clean extraction is not a nice-to-have. If an agent reads bad Markdown, it reasons over bad context.

AgentCrawl treats extraction quality as a product surface: output should be readable, stable, and easy to cite.

## Current Community baseline

The checked-in quality suite currently covers 23 fixtures and is used as a regression gate before release work. The report is local and reproducible; it is not a public claim that AgentCrawl beats another product.

```bash
python -m benchmarks.quality_report
```

Expected release-candidate shape:

```text
fixture_count: 23
minimum_score: 85
failed: 0
```

## What the benchmarks measure 📏

The benchmark suite focuses on agent-ready output, not only on whether a URL returned HTML.

Core checks cover:

- non-empty Markdown output;
- expected text presence;
- boilerplate reduction;
- link and canonical URL correctness;
- table preservation, including headers, separators, row values, and empty cells;
- code block preservation, including fenced blocks and language tags from common HTML classes;
- metadata and provenance presence;
- scrape output size and structure metrics;
- retry and failure classification where relevant.

For crawl jobs, checks cover:

- pages discovered;
- pages completed;
- pages failed;
- retry attempts;
- pagination correctness;
- cancellation behavior;
- state recovery after restart.

## Fixture categories

Checked-in HTML fixtures are preferred for CI stability. Optional live-url runs can be used for release checks and product comparisons.

```text
tests/fixtures/quality/
```

Current fixture categories covered in CI:

```text
documentation
article
ecommerce
forum/discussion
tables
blogs
canonical/provenance
messy documentation pages
noisy articles with inline ads/newsletter blocks
complex product pages with variants, tables, JSON-LD Product metadata, and noisy recommendations
adversarial ecommerce pages with schema graphs, offers arrays, aggregate ratings, reviews, hidden duplicates, and recommendation rails
API reference pages with multiple fenced code blocks and parameter tables
adversarial API references with shell commands, JSON responses, unlabeled code fences, blockquotes, and noisy docs navigation
media-rich articles with figures, captions, blockquotes, and read-more cards
adversarial media/news articles with captions, blockquotes, inline tables, JSON snippets, sponsor promos, related-story rails, and hidden duplicates
technical reference pages where a huge generated index precedes the real specification content
nested documentation layouts where <main> contains internal TOCs, sidebars, and related cards
adversarial documentation shells with nested sticky rails, duplicated hidden text, captions, tables, and fenced code
SPA/browser-rendered snapshots with tables, code blocks, and rendered-only content
```

The assertions live in `tests/test_quality_fixtures.py` and verify expected content, boilerplate removal, metadata presence, extraction size metadata, provenance fields, link counts, Markdown structure, and non-corrupted plain text.

## Report fields

`benchmarks/quality_report.py` emits JSON with:

- AgentCrawl version;
- fixture count;
- minimum score;
- average score;
- per-fixture pass/fail;
- Markdown/text character counts;
- link counts;
- missing expected content;
- boilerplate leaks;
- missing metadata;
- Markdown structure errors;
- quality check breakdown.

## Comparisons with other tools

The public comparison with Crawl4AI, Scrapling and Firecrawl (real websites, quality on checked pages, versions, run ids and how to reproduce it) is in [BENCHMARKS.md](BENCHMARKS.md). This page covers the regression suite and the tooling behind those numbers.

Rules the comparison follows: differences are tested (a gap with p > 0.05 is written as a tie), the expectations for the randomly drawn pages are written and committed before any tool runs on them, and challenge or block text returned as if it were the page is counted as junk, separately from an honest failure.

### Neutral corpus (`benchmarks/corpus/neutral.json`)

The fixtures above are written by us, so they cannot settle a comparison on their own. The neutral corpus is a versioned list of real, accessible public pages that nobody here controls: 12 pages chosen by this project (docs, references, an article, a blog post, a sandbox product page) and pages drawn at random from a Tranco sample by `benchmarks/draw_neutral.py`, with mechanical filters only.

```bash
python -m benchmarks.snapshot                      # fetch each page once, record SHA-256
python -m benchmarks.compare --corpus benchmarks/corpus/neutral.json
```

Snapshots are third-party content: they stay out of git (`benchmarks/corpus/snapshots/`), and `snapshots/index.json` records URL, final URL, status, size, SHA-256 and fetch time. `compare --corpus` prints those hashes with the results, so a published number names the exact bytes it was scored on. A page is scored only once someone has reviewed its snapshot and filled in its `expected` (and optionally `excluded`) signals; until then it is listed as `unscored` instead of counting as a perfect recall. Text signals are matched after removing inline Markdown (links, inline code, emphasis, escapes) from the output, so keeping formatting never costs a tool recall.

### Offline comparison lane (`benchmarks/compare.py`)

```bash
python -m pip install trafilatura crawl4ai   # optional comparison tools, in a separate venv
python -m benchmarks.compare --json results.json
```

Every tool receives the same raw HTML from `tests/fixtures/quality`, so the run needs no network or keys and is repeatable for the same tool versions. It reports text recall, structure recall (tables and code fences in any valid Markdown style), fence-language recall, noise leakage, output tokens and latency, and prints the tool versions, commit and environment it ran with.

Read the results with their bias in mind: these fixtures were written by this project and AgentCrawl's extractor is tuned against them, so a perfect AgentCrawl score here is a **regression guard**, not evidence of general superiority. Crawl4AI's `raw_markdown` is a full-page conversion by design; compare main-content extraction against its `fit_markdown` variant. For third-party pages, see the neutral corpus above and [BENCHMARKS.md](BENCHMARKS.md).
