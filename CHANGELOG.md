# Changelog

## 0.1.0 - Unreleased

- Initial AgentCrawl Community release candidate.
- Local and HTTP scraping with browser fallback.
- Main-content Markdown extraction with semantic container selection, text-rich fallback blocks, and boilerplate reduction.
- Markdown table preservation and fenced code blocks with language tags from common HTML classes.
- Local document ingestion for Markdown, text, JSON, XML/RSS/Atom, and PDF-to-Markdown through the optional `docs` extra.
- Mapping, crawling, persistent jobs, progress, and cancellation.
- Cache controls and statistics.
- Python client, CLI, and MCP integration.
- Authentication, SSRF protections, unsafe redirect blocking, and safe server defaults.
- Safety baseline fixes for text normalization, sitemap discovery, PDF limits, scrape error behavior, URL validation, and crawl failure filtering.
- Package version export, wheel/sdist build verification, clean install smoke tests, and GHCR Docker workflow.
