# Roadmap

AgentCrawl is focused on one job first: help agents read and crawl websites reliably from infrastructure you control.

This roadmap is directional. Public performance or quality claims need reproducible tests before they appear in docs or marketing.

## Current focus

- Phase 2 extraction quality: fixture scoring thresholds, richer provenance metadata, harder checked-in fixtures, and cleaner main-content output before comparative claims.
- Stable web/document-to-Markdown extraction for known URLs and local files.
- HTTP-first scraping with browser fallback when a page needs JavaScript.
- Durable crawl jobs with checkpoints, retries, cancellation, pagination, events, and failure inspection.
- Self-hosted API and MCP tools that agents can call directly.
- Safer operations: auth, SSRF protection, cache, usage stats, backup, restore, Docker, and diagnostics.

## Community phase status

### Completed

- Critical safety and behavior cleanup for the Community alpha baseline: text normalization, sitemap index expansion, sitemap discovery from `robots.txt`, IDNA domain normalization, sliding-window API rate limiting, safer URL validation, unsafe redirect blocking, PDF safety limits, and clearer scrape error behavior.
- Main-content extraction for semantic containers (`main`, `article`) and text-rich fallback blocks.
- Boilerplate reduction for page chrome, cookie banners, sidebars, related posts, hidden content, and unsafe tags.
- Markdown table preservation with headers, separators, and cell values.
- Fenced code block preservation with language tags from common HTML classes.
- Local document ingestion for Markdown, text, JSON, XML/RSS/Atom, and PDF-to-Markdown through the optional `docs` extra.
- Package version export, wheel build verification, Docker build workflow for GHCR, and `agentcrawl doctor` verification.
- Initial Phase 2 extraction quality baseline: 14 checked-in report fixtures plus a browser-rendered SPA shell/snapshot pair, fixture scoring threshold, JSON quality report, provenance metadata, JSON-LD/Product schema extraction, Markdown structure metrics, and harder noisy docs/article/product/API-reference/nested-sidebar/rendered cases.

### Next community priorities

- Push extraction quality further with live release smoke targets and more hostile page layouts.
- Public distribution readiness: PyPI release checks, public GHCR image, clean install smoke tests, and release notes.
- More copy-paste examples for CLI, Python, HTTP, Docker, MCP, and common agent workflows.
- Common office formats and optional OCR.
- Browser workflows: bounded waits, resource blocking, init scripts, click, scroll, type, screenshots, sessions, and cleanup guarantees.

## Competitive priorities

See [docs/COMPARISON.md](docs/COMPARISON.md) for the public positioning against Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, and Stagehand.

### 1. Prove extraction quality

- Add a fixture corpus for repeatable quality checks.
- Publish benchmark reports using [docs/QUALITY_BENCHMARKS.md](docs/QUALITY_BENCHMARKS.md).
- Strengthen main-content extraction for documentation, news, commerce pages, forums, tables, and code blocks.
- Add more metadata and provenance so agents can cite where content came from.

### 2. Improve adoption surface

- Add cookbook examples from [docs/EXAMPLES.md](docs/EXAMPLES.md).
- Publish a PyPI package release.
- Publish a container image.
- Add release notes and issue templates.
- Add TypeScript examples before deciding whether a dedicated Node SDK is needed.

### 3. Better document input

- PDF ingestion for local files through the optional `docs` extra.
- Better handling for text, XML, JSON, and Markdown files.
- Common office formats.
- Optional OCR for image-only documents.

### 4. Browser workflows

- Bounded wait, click, scroll, type, and capture actions.
- Screenshot or capture output.
- Session and cookie handling for authorized workflows.
- Cleanup guarantees for browser processes, tabs, timeouts, and failed jobs.

### 5. Operations

- Named API keys with expiry, rotation, and revocation.
- More detailed metrics for usage, queues, failures, and storage.
- Upgrade and rollback guidance for persistent deployments.
- Release automation for packages and containers.

## Product standard

AgentCrawl is maintained so a new user can install it, scrape a page, run the API, connect MCP, diagnose problems, back up state, and restore state from the public docs alone.
