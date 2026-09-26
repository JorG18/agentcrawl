# Quality Benchmarks

Clean extraction is not a nice-to-have. If an agent reads bad Markdown, it reasons over bad context.

AgentCrawl treats extraction quality as a product surface: output should be readable, stable, and easy to cite.

## Current Community baseline

The checked-in quality suite currently covers 20 fixtures and is used as a regression gate before release work. The report is local and reproducible; it is not a public claim that AgentCrawl beats another product.

```bash
python -m benchmarks.quality_report
```

Expected release-candidate shape:

```text
fixture_count: 20
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

## Competitive benchmark policy

Comparisons against Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, or Stagehand should stay separated by product lane.

Community benchmark targets should be accessible public docs, API references, blogs, RFC/reference pages, non-protected ecommerce/product pages, and pages without active browser/proxy challenges. Community does **not** lose for detecting a protected page honestly and returning a clear unsupported/challenge failure. Community **does** lose if it returns challenge, cookie, nav, or other garbage as if it were page content.

Protected pages (anti-bot challenges, proxy or geolocation requirements) are outside the Community benchmark lane.

Until repeated runs and fair scoring are ready, public docs should describe the quality standard and reproducible local checks, not claim broad superiority.

### Neutral corpus (`benchmarks/corpus/neutral.json`)

The fixtures above are written by us, so they cannot settle a comparison on their own. The neutral corpus is a versioned list of real, accessible public pages (docs, API references, RFCs and specs, an encyclopedia article, a blog post, a scraping-sandbox product page) that nobody here controls.

```bash
python -m benchmarks.snapshot                      # fetch each page once, record SHA-256
python -m benchmarks.compare --corpus benchmarks/corpus/neutral.json
```

Snapshots are third-party content: they stay out of git (`benchmarks/corpus/snapshots/`), and `snapshots/index.json` records URL, final URL, status, size, SHA-256 and fetch time. `compare --corpus` prints those hashes with the results, so a published number names the exact bytes it was scored on. A page is scored only once someone has reviewed its snapshot and filled in its `expected` (and optionally `excluded`) signals; until then it is listed as `unscored` instead of counting as a perfect recall. Text signals are matched after removing inline Markdown (links, inline code, emphasis, escapes) from the output, so keeping formatting never costs a tool recall.

### Results on the neutral corpus (0.4.5)

Run in GitHub Actions on 2026-09-26 ([benchmark workflow](../.github/workflows/benchmark.yml), Python 3.12, AgentCrawl 0.4.5, Crawl4AI 0.9.4, trafilatura 2.2.0, html2text 2025.4.15), 12 pages, same snapshot bytes for every tool (SHA-256 in the run's `benchmark-corpus.json`):

| tool | text recall | structure recall | code language | noise leakage | clean pages | total tokens |
|---|---|---|---|---|---|---|
| AgentCrawl | 100% | 100% | 100% | 8.3% | 11/12 | 207,358 |
| Crawl4AI `fit_markdown` | 100% | 100% | 0% | 7.6% | 9/12 | 341,773 |
| Crawl4AI `raw_markdown` | 100% | 100% | 0% | 83.3% | 2/12 | 379,196 |
| trafilatura | 91.7% | 90% | 0% | 0% | 10/12 | 127,584 |
| html2text (plain) | 100% | 25% | 0% | 83.3% | 1/12 | 289,343 |

"Clean pages" means every signal found and nothing excluded leaked.

How to read this, including where it favours AgentCrawl:

- **The signals were written by this project**, from each snapshot's HTML, before looking at any tool's output. They are not independent. The same review also found six AgentCrawl extraction bugs, which 0.4.5 fixes, so AgentCrawl was improved against this corpus and its score here is optimistic.
- **Text recall ties.** AgentCrawl, Crawl4AI and plain html2text keep every checked sentence; trafilatura drops RFC 9110's body and a PostgreSQL passage.
- **Noise:** Crawl4AI's `fit_markdown` and AgentCrawl leak about the same amount of site chrome; Crawl4AI's `raw_markdown` is the whole page by design.
- **Tokens:** AgentCrawl's output is about 40% smaller than Crawl4AI's `fit_markdown` for the same content kept; trafilatura's is smaller still, because it drops code, tables and whole sections.
- **Code language tags:** only two pages declare a language, so that column rests on two fences.
- **What this does not measure:** JavaScript-heavy pages, crawling, speed at scale, or any of Crawl4AI's browser features. Latency here excludes browser start-up and is not a fair speed comparison.
- 12 pages is a small sample. Treat this as "competitive on accessible docs pages", not as a ranking.

Reproduce it with the benchmark workflow (it runs on pull requests that touch the benchmark and uploads the snapshots and every tool's output) or locally:

```bash
python -m benchmarks.snapshot
python -m benchmarks.compare --corpus benchmarks/corpus/neutral.json --dump-dir outputs
```

### Offline comparison lane (`benchmarks/compare.py`)

```bash
python -m pip install trafilatura crawl4ai   # optional comparison tools, in a separate venv
python -m benchmarks.compare --json results.json
```

Every tool receives the same raw HTML from `tests/fixtures/quality`, so the run needs no network or keys and is repeatable for the same tool versions. It reports text recall, structure recall (tables and code fences in any valid Markdown style), fence-language recall, noise leakage, output tokens and latency, and prints the tool versions, commit and environment it ran with.

Read the results with their bias in mind: these fixtures were written by this project and AgentCrawl's extractor is tuned against them, so a perfect AgentCrawl score here is a **regression guard**, not evidence of general superiority. Crawl4AI's `raw_markdown` is a full-page conversion by design; compare main-content extraction against its `fit_markdown` variant. A neutral corpus (third-party pages with independently written expectations) is required before any public comparison.
