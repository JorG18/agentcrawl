# Changelog

All notable changes to AgentCrawl Community will be documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.1 - 2026-06-26

Privacy and observability pillars land as opt-in Community
features. All additions are backwards-compatible: existing
callers that do not pass the new flags get identical behaviour
to 0.1.0.

### Added

- **Token Efficiency pillar** — every `ScrapeDocument.metadata`
  now exposes `estimated_tokens`, `raw_html_tokens_estimate`, and
  `raw_html_bytes`. The CLI accepts `--token-stats` on `scrape` and
  prints a Token Efficiency Report (extracted tokens, raw HTML
  tokens, savings %, raw HTML bytes) to stderr. Cheap, dependency-free
  `len/4` estimator; deliberately avoids tiktoken.
- **Audit / Airgap pillar** — `CrawlConfig` accepts `airgap`,
  `allowlist_domains`, and `audit` flags. The HTTP fetcher installs a
  urllib opener handler that validates every request against the
  target host (+ explicit comma-separated allowlist with `*.foo.com`
  wildcard support). `airgap=True` blocks any non-target request with
  `AirgapViolation`. `audit=True` records
  `metadata.audit_request_count`,
  `metadata.audit_third_party_request_count`,
  `metadata.audit_total_bytes`, and `metadata.audit_records` on the
  document. Env-driven via `AGENTCRAWL_AIRGAP`,
  `AGENTCRAWL_AIRGAP_ALLOWLIST`, and `AGENTCRAWL_AUDIT`.
- **Observable packaging** — `agentcrawl failures [filters]
  --export /path/to/failures.csv` writes the filtered failures
  listing to a CSV file (auto-creates parent dirs, deterministic
  header). Dependency-free: stdlib `csv`.

### Fixed

- Cookie-consent text inside generic containers (`<p>`, `<div>`,
  `<section>`, `<aside>`, `<span>`, `<small>`, `<li>` without a
  `cookie`/`consent` class) is now dropped at the node level before
  Markdown conversion. Legitimate documentation that discusses
  cookies as a feature (e.g. FastAPI's "Cookie Sessions") is
  preserved.
  Added fixture `tests/fixtures/quality/cookie_consent.html` and
  regression tests in `tests/test_parsing.py`.
- When `browser_fallback=true` and the configured `browser_backend`
  is `playwright` or `camofox`, `scrape()` retries once with the
  browser backend on a fresh 200-OK challenge page before reporting
  `client_challenge`. The retry is opt-in (gated on the user flag)
  and falls back silently to the original error if the browser
  itself is challenged or raises. The retry path is the existing
  local fallback and does NOT make Community a Cloudflare bypass;
  managed proxy rotation, residential IPs, and stealth
  challenge-solving remain in Enhanced/Hosted.
  Added `agentcrawl/browser_retry.py` and regression tests in
  `tests/test_browser_retry.py` covering 6 paths (no retry when
  source is non-remote, opt-out preserved, retry success, browser
  also challenged, browser raises, fetcher already a browser).

### Documentation

- `INSTALL_FOR_AGENTS.md`: replaced the `example.com` smoke test
  with `pypi.org/project/agentcrawl-ai` plus `agentcrawl doctor`,
  and documented `example.com` explicitly as the canonical
  Community boundary case (Cloudflare client challenge, `ok=False`,
  `error_type=client_challenge`).
- `README.md`: canonical quickstart now points at
  `https://pypi.org/project/agentcrawl-ai/`, with an explicit
  "Edge case: `example.com` returns a Cloudflare client challenge"
  block that explains the boundary without smoothing it over.
- `ROADMAP.md`: marked the cookie-consent filter, the opt-in
  browser retry, and the doc swaps as Completed; updated "Next
  community priorities" to name the three differentiation pillars.

### Verification

- `pytest`: 153 passed (127 original + 26 new), 1 non-blocking
  Starlette/httpx warning.
- `ruff check` OK; `ruff format --check` OK.
- `quality_report.py`: 20/20 fixtures @ 100.0 avg, 85 min.
- `agentcrawl doctor`: `local_scrape`, `agentcrawl_command`,
  `python` all `ok=true`. `remote_health` is intentionally off
  (no daemon, on-demand).

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
