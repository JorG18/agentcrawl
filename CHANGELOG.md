# Changelog

All notable changes to AgentCrawl Community will be documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.1 - Unreleased

In progress. No code changes yet for this version; this section will be filled as work lands. Targets:

- Tighten cookie-consent and similar boilerplate filtering so cookie-banner text is not passed through as extracted content.
- Allow `browser_fallback=true` to retry once on 200-OK protected pages before reporting `client_challenge`. Managed proxy rotation, residential IPs, and remote challenge-solving remain Enhanced/Hosted.
- Harden RFC/technical-reference main-content selection against generated index/TOC candidates when large reference pages are scraped.
- Refresh public docs once the three fixes above close the gap in the private severe benchmark for Community-target lanes. Public comparative claims require reproducible evidence first.
- Verify `pytest`, `ruff`, and the local quality report (`benchmarks/quality_report.py`) stay green for every change.

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
