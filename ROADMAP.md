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
- Cookie-consent node-level filter: drops text-only cookie-consent blocks inside generic containers, while preserving legitimate documentation that mentions cookies as a feature. (0.1.1)
- Opt-in browser fallback on 200-OK challenge pages: when `browser_fallback=true` and the configured `browser_backend` is `playwright` or `camofox`, Community retries once with the browser backend before reporting `client_challenge`. The retry is the existing local fallback path; it is not a Cloudflare bypass and managed proxy rotation, residential IPs, and remote challenge-solving remain outside Community. (0.1.1)
- README and INSTALL pointer swapped from `example.com` to `pypi.org/project/agentcrawl-ai/` for the canonical quickstart, and `example.com` is now documented explicitly as the boundary case (Cloudflare client challenge → `client_challenge`).
- Markdown table preservation and fenced code block preservation with language tags.
- Local document ingestion for Markdown, text, JSON, XML/RSS/Atom, and PDF-to-Markdown through the optional `docs` extra.
- `agentcrawl doctor`, `agentcrawl --version`, package build verification, and clean install smoke tests.
- Lightweight Docker image published through GHCR: `ghcr.io/jorg18/agentcrawl:latest`.
- Quality extraction hardening: 19 checked-in quality fixtures plus browser-rendered SPA shell/snapshot coverage, score threshold, JSON report, richer provenance, JSON-LD/Product schema extraction, Product rating extraction, hidden-class filtering, Markdown structure checks, and Markdown structure metrics. Cookie-consent fixture added in 0.1.1 (20 fixtures total).

## Next community priorities

These are follow-up areas for Community that stay under the self-hosted, governed-extraction boundary. They are not yet shipped; each one will land only when its verification criteria are met.

1. **Three differentiation pillars** — Token Efficiency (CLI/API/MCP/Python surface exposing `estimated_tokens`), Audit / Airgap (`--audit` flag + `AGENTCRAWL_AIRGAP=true` blocking any non-target request), and Observable packaging (`agentcrawl dashboard`/local read-only HTML over SQLite, `--export failures.csv`, and `--alert-on-failure` for local commands). Each pillar adds a verifiable surface and keeps the self-hosted/private boundary intact.
2. Keep the Community benchmark lane focused on accessible public docs, API references, blogs, RFC/reference pages, and non-protected ecommerce/product pages; do not turn it into broad competitor claims.
3. Maintain release smoke targets without turning them into unsupported public comparison claims.
4. Improve document ingestion beyond PDF only when it remains lightweight for Community.
5. Add examples only when they reflect verified Community behavior.

## Public launch readiness

Public marketing and visibility copies (Show HN, Reddit, blog, demo assets) are drafted in private planning docs and will roll out only after:

- the three quality fixes above close the gap in the private severe benchmark for Community-target lanes;
- the local smoke run from `tests/` is green and the quality report still holds at the 19-fixture baseline;
- `README.md` and the public docs do not make comparative quality claims unsupported by reproducible evidence.

Until those conditions are met, the README remains a precise technical quickstart, not a competitive landing page. Enhanced/Hosted features (managed browsers/proxies, schedules, webhooks, retained datasets, teams, billing, SSO/RBAC/audit, private networking) remain separate products and stay out of this roadmap.

## Enhanced / hosted priorities

Enhanced Local and Enhanced Hosted are planned separately for managed browser/proxy/challenge infrastructure, JS-heavy targets, schedules, webhooks, retained datasets, teams, usage/billing, and enterprise controls.

## Competitive priorities

See [docs/COMPARISON.md](docs/COMPARISON.md) for public positioning against Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, and Stagehand.

Competitive benchmarks remain deferred until the public install paths, examples, release docs, and smoke tests are stable. AgentCrawl should not claim superiority without reproducible evidence.

## Product boundary

Community is self-hosted. It should stay useful for single-node/local users who want control over extraction, jobs, cache, and MCP integration.

Managed hosted AgentCrawl is planned separately for managed browsers, proxies, schedules, webhooks, retained datasets, teams, usage/billing, and enterprise controls.

## Product standard

A new user should be able to install AgentCrawl, scrape a page, run the API, connect MCP, diagnose problems, back up state, and restore state from the public docs alone.
