# Roadmap

AgentCrawl is focused on one job first: help agents read and crawl websites reliably from infrastructure you control.

This roadmap is directional. Public performance or quality claims need reproducible tests before they appear in docs or marketing.

## Current focus

- Stable web-to-Markdown extraction for known URLs.
- HTTP-first scraping with browser fallback when a page needs JavaScript.
- Durable crawl jobs with checkpoints, retries, cancellation, pagination, events, and failure inspection.
- Self-hosted API and MCP tools that agents can call directly.
- Safer operations: auth, SSRF protection, cache, usage stats, backup, restore, Docker, and diagnostics.

## Next

### Better document input

- PDF ingestion.
- Common office formats.
- Better handling for text, XML, JSON, and Markdown files.
- Optional OCR for image-only documents.

### Extraction quality

- A fixture corpus for repeatable quality checks.
- Stronger main-content extraction for documentation, news, commerce pages, forums, tables, and code blocks.
- More metadata and provenance so agents can cite where content came from.

### Browser workflows

- Bounded wait, click, scroll, type, and capture actions.
- Session and cookie handling for authorized workflows.
- Cleanup guarantees for browser processes, tabs, timeouts, and failed jobs.

### Operations

- Named API keys with expiry, rotation, and revocation.
- More detailed metrics for usage, queues, failures, and storage.
- Upgrade and rollback guidance for persistent deployments.
- Release automation for packages and containers.

## Release standard

A release is ready when a new user can install AgentCrawl, scrape a page, run the API, connect MCP, diagnose problems, back up state, and restore state from the public docs alone.
