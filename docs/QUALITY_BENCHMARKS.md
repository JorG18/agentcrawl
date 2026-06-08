# Quality Benchmarks

AgentCrawl should only make public quality or performance claims when they can be reproduced from this repository.

This document defines the benchmark shape before the benchmark runner is implemented.

## Goals

- Measure Markdown usefulness for agents, not raw HTML coverage alone.
- Catch regressions in main-content extraction, tables, links, code blocks, metadata, and crawl durability.
- Compare AgentCrawl against realistic alternatives when the user configures their own credentials or local installs.
- Keep the baseline small enough to run in CI and extensible enough for deeper release checks.

## Fixture Corpus

The first public corpus should include stable pages or checked-in HTML fixtures for these categories:

| Category | What to verify |
| --- | --- |
| Documentation | Headings, code blocks, sidebars removed, internal links preserved. |
| News or article | Main article extracted, navigation and related links reduced. |
| Ecommerce | Product title, price text, variants, availability, breadcrumbs. |
| Tables | Header/body alignment, row completeness, no flattened unreadable table text. |
| Forums or discussion | Thread title, author/date metadata when visible, comments in order. |
| Blogs | Title, author, date, body, canonical URL. |
| JavaScript rendered page | Browser fallback produces content unavailable to HTTP fetch. |
| Redirects and canonical URLs | Final URL, metadata, and content provenance are preserved. |
| Failure cases | Timeouts, blocked pages, invalid URLs, private network rejections. |

Prefer checked-in HTML fixtures for CI stability. Use live URLs only in an optional release benchmark job.

## Metrics

Minimum metrics:

- non-empty Markdown rate;
- expected text presence;
- forbidden boilerplate presence;
- link count and canonical URL correctness;
- table preservation score;
- code block preservation score;
- metadata presence;
- scrape duration;
- cache hit behavior;
- retry and failure classification behavior.

For crawl jobs:

- pages discovered;
- pages completed;
- pages failed;
- retry attempts;
- pagination correctness;
- cancellation behavior;
- resume after restart behavior.

## Comparison Targets

Comparison adapters should be optional and skipped unless configured:

- Firecrawl API;
- Crawl4AI local install;
- Jina Reader API;
- ScrapeGraphAI local install;
- Crawlee local crawler.

The report should not claim another project is worse unless the benchmark setup and versions are recorded.

## Report Format

Each run should produce:

```text
benchmarks/reports/YYYY-MM-DD-summary.md
benchmarks/reports/YYYY-MM-DD-results.json
```

The Markdown report should include:

- AgentCrawl version and git SHA;
- Python version and OS;
- enabled extras;
- fixture list;
- per-fixture results;
- aggregate pass/fail table;
- comparison target versions;
- known caveats.

## Release Gate

A public release should not add stronger extraction claims unless:

- the fixture corpus passes locally;
- CI runs the fixture corpus without live network dependencies;
- any public comparison claim links to a report;
- failures are documented as known limitations or fixed.

## First Implementation Tasks

1. Add `benchmarks/fixtures/` with checked-in HTML pages.
2. Add expected-output assertions as JSON or YAML.
3. Add a `python -m benchmarks.run` command.
4. Generate JSON and Markdown reports.
5. Add CI for fixture-only benchmark checks.
6. Add optional live comparison adapters behind environment variables.
