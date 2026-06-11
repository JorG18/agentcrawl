# Quality Benchmarks

Clean extraction is not a nice-to-have. If an agent reads bad Markdown, it reasons over bad context.

AgentCrawl treats extraction quality as a product surface: output should be readable, stable, and easy to cite.

## Current Community baseline

The checked-in quality suite currently covers 19 fixtures and is used as a regression gate before release work. The report is local and reproducible; it is not a public claim that AgentCrawl beats another product.

```bash
python benchmarks/quality_report.py
```

Expected release-candidate shape:

```text
fixture_count: 19
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

Comparisons against Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, or Stagehand should wait until:

1. public install paths are stable;
2. examples/cookbook are complete;
3. release smoke tests are documented;
4. live target selection is reproducible;
5. scoring rules are public and fair.

Until then, public docs should describe the quality standard and reproducible local checks, not claim superiority.
