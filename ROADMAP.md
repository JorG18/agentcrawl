# Roadmap

AgentCrawl is focused on one job first: help agents read and crawl websites reliably from infrastructure you control.

This roadmap is directional. Public performance or quality claims need reproducible tests before they appear in docs or marketing.

## Current focus

- Stable web-to-Markdown extraction for known URLs.
- HTTP-first scraping with browser fallback when a page needs JavaScript.
- Durable crawl jobs with checkpoints, retries, cancellation, pagination, events, and failure inspection.
- Self-hosted API and MCP tools that agents can call directly.
- Safer operations: auth, SSRF protection, cache, usage stats, backup, restore, Docker, and diagnostics.

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

- PDF ingestion.
- Common office formats.
- Better handling for text, XML, JSON, and Markdown files.
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
