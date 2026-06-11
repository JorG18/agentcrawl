# Roadmap

AgentCrawl focuses on one job first: help AI agents read and crawl web content reliably from infrastructure you control.

This roadmap is directional. Public performance or quality claims need reproducible tests before they appear in docs or marketing.

## Current community status

AgentCrawl Community is a serious self-hosted alpha/release-candidate surface. The core paths are in place:

- CLI, Python library, HTTP API, Docker/GHCR image, and MCP server.
- HTTP-first scraping with optional browser/Camofox fallback when needed.
- Durable crawl jobs with checkpoints, retries, cancellation, pagination, events, and failure inspection.
- SQLite-backed cache, usage, jobs, events, crawl failures, and extracted documents.
- Safer server defaults: bearer auth, `robots.txt` support, SSRF protections, unsafe redirect blocking, and private-network controls.
- Quality extraction baseline: checked-in fixtures, quality report, provenance metadata, JSON-LD/Product extraction, Markdown table/code preservation, and noisy-layout handling.
- Distribution readiness: wheel/sdist checks, clean install smoke tests, CI, lightweight Docker image, and GHCR publication.

## Completed

- Critical safety and behavior cleanup: text normalization, sitemap index expansion, sitemap discovery from `robots.txt`, IDNA domain normalization, sliding-window API rate limiting, safer URL validation, unsafe redirect blocking, PDF safety limits, and clearer scrape error behavior.
- Main-content extraction for semantic containers and text-rich fallback blocks.
- Boilerplate reduction for page chrome, cookie banners, sidebars, related posts, hidden content, and unsafe tags.
- Markdown table preservation and fenced code block preservation with language tags.
- Local document ingestion for Markdown, text, JSON, XML/RSS/Atom, and PDF-to-Markdown through the optional `docs` extra.
- `agentcrawl doctor`, `agentcrawl --version`, package build verification, and clean install smoke tests.
- Lightweight Docker image published through GHCR: `ghcr.io/jorg18/agentcrawl:latest`.
- Quality extraction hardening: 18 checked-in quality fixtures plus browser-rendered SPA shell/snapshot coverage, score threshold, JSON report, richer provenance, JSON-LD/Product schema extraction, Product rating extraction, hidden-class filtering, Markdown structure checks, and Markdown structure metrics.

## Next community priorities

1. Publish the first PyPI release after final token/config verification.
2. Finish the minimal examples/cookbook set for CLI, Python, HTTP API, Docker, MCP, and browser-rendered pages.
3. Add release notes and final public release tag.
4. Add live smoke targets for release checks without turning them into unsupported benchmark claims.
5. Improve document ingestion beyond PDF only when it remains lightweight for Community.

## Competitive priorities

See [docs/COMPARISON.md](docs/COMPARISON.md) for public positioning against Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, and Stagehand.

Competitive benchmarks remain deferred until the public install paths, examples, release docs, and smoke tests are stable. AgentCrawl should not claim superiority without reproducible evidence.

## Product boundary

Community is self-hosted. It should stay useful for single-node/local users who want control over extraction, jobs, cache, and MCP integration.

Managed hosted AgentCrawl is planned separately for managed browsers, proxies, schedules, webhooks, retained datasets, teams, usage/billing, and enterprise controls.

## Product standard

A new user should be able to install AgentCrawl, scrape a page, run the API, connect MCP, diagnose problems, back up state, and restore state from the public docs alone.
