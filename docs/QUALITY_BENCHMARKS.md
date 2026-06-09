# Quality Benchmarks

Clean extraction is not a nice-to-have. If an agent reads bad Markdown, it reasons over bad context.

AgentCrawl treats extraction quality as a product surface: output should be readable, stable, and easy to cite.

## What The Benchmarks Measure 📏

The benchmark suite focuses on agent-ready output, not only on whether a URL returned HTML.

Core checks cover:

- non-empty Markdown output;
- expected text presence;
- boilerplate reduction;
- link and canonical URL correctness;
- table preservation, including headers, separators, row values, and empty cells;
- code block preservation, including fenced blocks and language tags from common HTML classes;
- metadata presence;
- scrape duration;
- cache behavior;
- retry and failure classification.

For crawl jobs, checks cover:

- pages discovered;
- pages completed;
- pages failed;
- retry attempts;
- pagination correctness;
- cancellation behavior;
- state recovery after restart.

## Fixture Categories

The benchmark corpus is organized around page types agents commonly read:

| Category | What is evaluated |
| --- | --- |
| Documentation | Headings, code blocks, sidebars, internal links, and page hierarchy. |
| News or articles | Main article extraction, title, date, author, and navigation reduction. |
| Ecommerce | Product title, price text, variants, availability, breadcrumbs, and metadata. |
| Tables | Header/body alignment, row completeness, and readable Markdown structure. |
| Forums or discussions | Thread title, visible author/date metadata, and comment order. |
| Blogs | Title, author, date, body, canonical URL, and links. |
| JavaScript rendered pages | Browser fallback content that is unavailable through HTTP-only fetches. |
| Redirects and canonical URLs | Final URL tracking, metadata, and provenance. |
| Local documents | Markdown/text pass-through, JSON/XML fenced output, and page-by-page PDF Markdown extraction. |
| Failure cases | Timeouts, invalid URLs, blocked pages, private-network rejections, missing optional extras, and SSRF protections. |

Checked-in HTML fixtures are preferred for CI stability. Optional live-url runs can be used for release checks and product comparisons.

## Comparison Runs

AgentCrawl can be compared against other tools when their adapters and credentials are configured by the user:

- Firecrawl API;
- Crawl4AI local install;
- Jina Reader API;
- ScrapeGraphAI local install;
- Crawlee local crawler.

Comparison reports record tool versions, configuration, enabled extras, target URLs, and caveats. Public claims link to reproducible reports instead of relying on anecdotal results.

## Report Format

Benchmark runs produce machine-readable and human-readable output:

```text
benchmarks/reports/YYYY-MM-DD-summary.md
benchmarks/reports/YYYY-MM-DD-results.json
```

The Markdown summary includes:

- AgentCrawl version and git SHA;
- Python version and OS;
- enabled extras;
- fixture list;
- per-fixture results;
- aggregate pass/fail table;
- comparison target versions;
- known caveats.

## Release Standard

Release-quality extraction claims meet these conditions:

- fixture checks pass locally;
- CI runs fixture checks without live-network dependencies;
- live comparison claims include tool versions and configuration;
- known failures are documented or tracked;
- stronger public claims link to benchmark evidence.

## Adding New Benchmarks

When adding a new benchmark, include:

1. A stable fixture or clearly marked live URL.
2. Expected text assertions.
3. Boilerplate excluded from the desired output.
4. Metadata expectations.
5. Any special fetcher requirement, such as browser fallback.
6. A short explanation of the product behavior being protected.
