# Changelog

## 0.1.0 - Unreleased

- Initial AgentCrawl Community release candidate.
- CLI, Python library, HTTP API, Docker/GHCR image, and MCP integration.
- Local and HTTP scraping with optional browser/Camofox fallback.
- Main-content Markdown extraction with semantic container selection, text-rich fallback blocks, and boilerplate reduction.
- Markdown table preservation and fenced code blocks with language tags from common HTML classes.
- Local document ingestion for Markdown, text, JSON, XML/RSS/Atom, and PDF-to-Markdown through the optional `docs` extra.
- Mapping, crawling, persistent jobs, progress, cancellation, event history, crawl failures, and selective failure retries.
- Cache controls, usage reporting, operational stats, backup, and restore.
- Authentication, SSRF protections, unsafe redirect blocking, private-network controls, and safe server defaults.
- Safety baseline fixes for text normalization, sitemap discovery, PDF limits, scrape error behavior, URL validation, and crawl failure filtering.
- Package version export, wheel/sdist build verification, `twine check`, and clean install smoke tests for base/server/MCP/docs extras.
- Lightweight default Docker image based on `python:3.12-slim`, published through GHCR as `ghcr.io/jorg18/agentcrawl:latest`.
- GitHub Actions Docker workflow builds, smoke-tests, and publishes GHCR images for `main`, tags, and commit SHAs.
- README quickstart refreshed around CLI, Python, MCP, Docker/API, and Community scope.
- Quality report baseline: 19 checked-in fixtures, minimum score threshold 85, current local average 100.0, richer provenance metadata, JSON-LD/Product schema extraction, Markdown structure metrics, and noisy-layout coverage.
- Phase 2 hardening: protected/challenge pages are classified as honest failures instead of scraped content, and technical reference extraction avoids generated index/TOC candidates when selecting main content.
