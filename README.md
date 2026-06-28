# AgentCrawl

[![CI](https://github.com/JorG18/agentcrawl/actions/workflows/ci.yml/badge.svg)](https://github.com/JorG18/agentcrawl/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

![AgentCrawl README hero](assets/readme-hero.png)

🕷️ **A small self-hosted crawler for AI agents.**

AgentCrawl gives agents a simple way to read normal web pages without pasting raw HTML into chat or routing every URL through a hosted scraper. It turns pages and local documents into Markdown, text, links, metadata, JSON-LD, and crawl results. It runs from the CLI, Python, Docker/API, or as an MCP server.

The project is early, intentionally modest, and being worked on steadily: accessible pages first, clean output, local state, honest failures.

```bash
pip install agentcrawl-ai
agentcrawl scrape https://pypi.org/project/agentcrawl-ai/
```

### Edge case: `example.com` returns a Cloudflare client challenge

```bash
$ agentcrawl scrape https://example.com
# → ok=False, error_type=client_challenge
```

`example.com` sits behind Cloudflare and returns a client challenge on
most networks. AgentCrawl **detects** challenge pages and returns an
honest `client_challenge` error rather than scraping challenge DOM as
content. This is the Community product boundary, not a bug. Managed
browser/proxy/challenge handling belongs to Enhanced/Hosted and
requires an operator or paid plan.

To verify Community works on your machine, point it at any accessible
docs page: FastAPI, GitHub, Wikipedia, the RFC editor.

## Pick your path 🚀

### Agents: MCP 🤖

```bash
python -m pip install "agentcrawl-ai[browser]"
agentcrawl doctor
agentcrawl mcp
```

MCP tools cover `scrape_url`, `map_site`, `crawl_site`, job status, cancellation, event history, failure inspection, selective retries, usage, and cache control. Coding agents should follow [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md).

### Developers: Python + CLI 🧪

```bash
pip install agentcrawl-ai
agentcrawl scrape https://pypi.org/project/agentcrawl-ai/
```

```python
from agentcrawl import AgentCrawl

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("https://pypi.org/project/agentcrawl-ai/")

print(document.markdown)
print(document.metadata)
```

### Servers: Docker + API 🐳

```bash
docker run --rm -p 8000:8000 \
  -e AGENTCRAWL_API_KEYS="replace-with-a-long-random-key" \
  ghcr.io/jorg18/agentcrawl:latest

curl http://127.0.0.1:8000/health
# Read-only local dashboard:
# open http://127.0.0.1:8000/dashboard
# curl http://127.0.0.1:8000/api/dashboard/summary
```

Or with Compose:

```bash
cp .env.example .env
# Replace AGENTCRAWL_API_KEYS and AGENTCRAWL_API_KEY in .env
docker compose up -d
curl http://127.0.0.1:8000/health
```

## Why AgentCrawl? 🕸️

Agents often need web context, but raw HTML is a mess. A useful page can arrive mixed with navigation, cookie text, related links, footer links, scripts, and layout junk.

AgentCrawl is a small local layer for that. Give it a URL, get something an agent can read, and keep the cache, jobs, and failures in your own environment.

What works today:

- 🧹 Known URL in, clean Markdown out: main-content extraction, tables, code blocks, links, metadata, and provenance.
- ⚡ HTTP first: fast default extraction without starting a browser.
- 🧱 Durable crawls: SQLite jobs with checkpoints, pagination, cancellation, events, retries, and failure inspection.
- 📦 Local state: cache, usage, jobs, events, crawl failures, and extracted documents stay with you.
- 📊 Read-only dashboard: generate static HTML from SQLite with `agentcrawl dashboard` or open `/dashboard` on the API server.
- 🔒 Safer API defaults: bearer auth, `robots.txt` support, SSRF protections, unsafe redirect blocking, and private-network controls.
- 🤖 Agent-facing interfaces: CLI, Python, HTTP API, Docker, and MCP.

## What Community includes 🧰

AgentCrawl Community is the self-hosted trust layer:

| Included | Notes |
| --- | --- |
| CLI | Scrape, crawl, inspect jobs, manage cache, backup, restore. |
| Python library | Local use from scripts and agent runtimes. |
| HTTP API | FastAPI server for self-hosted deployments. |
| MCP | Standards-based stdio MCP server for agent clients. |
| Docker / GHCR | Public image built and smoke-tested by GitHub Actions. |
| Durable crawls | SQLite jobs, events, checkpoints, retries, and failure records. |
| Local dashboard | Read-only static HTML over SQLite via `agentcrawl dashboard` and `/dashboard`. |
| Quality extraction | Markdown, links, metadata, JSON-LD/provenance, tables, code blocks. |
| Basic browser fallback | Optional local browser/Camofox path, not required for the default image. |
| Lightweight docs | Install, examples, operations, release, quality notes. |

Community is self-hosted. It is designed for accessible web content, local/private workflows, and honest failure reporting when a page is protected by anti-bot or browser challenges. Community may detect challenge pages and return a clear `client_challenge`/unsupported failure; it does not promise to bypass them. The optional browser path is local bring-your-own rendering, not a managed browser pool. Managed browsers, proxies, schedules, webhooks, retained datasets, teams, billing, and enterprise controls belong to planned enhanced/hosted tiers rather than the Community runtime.

## Community boundary 🚧

Community is the open, self-hosted trust layer. It should stay excellent for accessible public HTML, local documents, docs, API references, articles, and reference pages. It should not grow into a free hosted-scraping platform.

Community should report protected pages honestly instead of returning challenge text as content. For protected pages, use a local browser fallback when that is enough; if the target requires managed browser/proxy/challenge infrastructure, treat it as an Enhanced/Hosted use case. Community does not include hosted infrastructure, managed browser pools, proxy networks, geolocation, stealth, schedules, webhooks, retained datasets, teams, billing, or enterprise controls.

## Extraction quality 🧹

The Community engine focuses on stable, agent-ready Markdown before benchmark claims:

- selects semantic content from `<main>`, `<article>`, documentation/content containers, or text-rich fallback blocks;
- removes unsafe and noisy page chrome such as scripts, styles, hidden content, nav, footer, cookie banners, sidebars, and related-post blocks;
- preserves Markdown tables with headers and cell values;
- preserves fenced code blocks and language tags from common classes such as `language-python` and `lang-javascript`;
- attaches extraction provenance such as source/final URL, selected content hint, selection score, candidate count, content hash, extraction strategy, JSON-LD/schema fields, Product offer/rating fields, and output size/structure metadata;
- validates extraction quality against checked-in fixtures with a minimum score threshold and Markdown structure checks.

Run the report locally:

```bash
python benchmarks/quality_report.py
```

## HTTP API 🌐

Authentication is enabled by default. Configure at least one API key before exposing the server:

```bash
export AGENTCRAWL_API_KEYS="replace-with-a-long-random-key"
python -m pip install "agentcrawl-ai[browser]"
agentcrawl serve --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Scrape a URL:

```bash
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer replace-with-a-long-random-key" \
  -H "content-type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown","links","metadata"]}'
```

Main endpoints:

```text
GET    /health
GET    /dashboard
GET    /api/dashboard/summary
POST   /v1/scrape
POST   /v1/map
POST   /v1/crawl
GET    /v1/jobs/{job_id}
GET    /v1/jobs/{job_id}/events
DELETE /v1/jobs/{job_id}
GET    /v1/failures
GET    /v1/jobs/{job_id}/failures
POST   /v1/jobs/{job_id}/failures/retry
POST   /v1/extract
GET    /v1/usage
GET    /v1/stats
DELETE /v1/cache
```

OpenAPI docs are available at `/docs` when the server is running.

## Local dashboard 📊

Generate a dependency-free static HTML snapshot from any AgentCrawl SQLite database:

```bash
agentcrawl dashboard --db agentcrawl.db --output dashboard.html
```

When the API server is running, open the same read-only view at `/dashboard` or fetch JSON at `/api/dashboard/summary`. The dashboard reports job status, crawl queue, open failures, cache domains, and usage units without sending data to any hosted service.

## Crawl jobs 🧭

Start an asynchronous crawl:

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
```

HTTP clients can attach an idempotency key so retries return the original job instead of starting a duplicate:

```bash
curl http://127.0.0.1:8000/v1/crawl \
  -H "authorization: Bearer replace-with-a-long-random-key" \
  -H "content-type: application/json" \
  -H "Idempotency-Key: docs-crawl-2026-06-06" \
  -d '{"url":"https://example.com","max_pages":25,"max_depth":2}'
```

Running jobs checkpoint their queue, visited URLs, retry attempts, progress, and extracted documents in SQLite. Transient page failures use persisted exponential backoff without occupying a crawl worker. They are reclaimed after a service restart.

Read completed documents page by page:

```bash
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

Run a local command when a crawl finishes with terminal failures:

```bash
agentcrawl crawl https://example.com \
  --alert-on-failure \
  --cmd 'python notify.py'
```

The command receives JSON on stdin with `source`, `failure_count`, and `failures`.

Inspect or cancel a job:

```bash
agentcrawl --remote job JOB_ID
agentcrawl --remote job-cancel JOB_ID
```

`/v1/stats` reports queue readiness, delayed retries, running and cancelling jobs, crawl failures by status, open retryable failures, and open failures by error type.

## Local documents 📄

Community supports local document ingestion without sending file contents to a hosted parser:

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
python -m pip install "agentcrawl-ai[browser]"
agentcrawl scrape ./report.pdf
```

Current document support:

| Input | Support |
| --- | --- |
| HTML | Main-content Markdown extraction. |
| Markdown | Passed through as Markdown. |
| Text | Passed through as plain Markdown text. |
| JSON | Pretty-printed inside a fenced `json` block. |
| XML/RSS/Atom | Preserved inside a fenced `xml` block. |
| PDF | Extracted page-by-page to Markdown with the optional `docs` extra. Enforces size/page safety limits and rejects encrypted PDFs. |

## Browser rendering

The default package and default Docker image use HTTP extraction. Add browser rendering only when a site needs JavaScript:

```bash
python -m pip install "agentcrawl-ai[browser]"
playwright install chromium
```

AgentCrawl also supports an optional external Camofox REST backend:

```bash
export AGENTCRAWL_BROWSER_BACKEND=camofox
export AGENTCRAWL_CAMOFOX_URL=http://127.0.0.1:9377
export AGENTCRAWL_CAMOFOX_ACCESS_KEY=replace-if-access-control-is-enabled
```

## Cache ⚡

Disable cache for one scrape or choose a TTL of up to 30 days:

```json
{"url":"https://example.com","cache":false}
```

```json
{"url":"https://example.com","cache_ttl_seconds":3600}
```

Clear all cache entries or filter by domain or exact URL:

```bash
agentcrawl --remote cache-clear
agentcrawl --remote cache-clear --domain example.com
agentcrawl --remote cache-clear --url https://example.com/page
```

## Backups 💾

Use SQLite online backup before deployment or migration:

```bash
agentcrawl backup --db agentcrawl.db --output-dir ./backups
```

Pass `--env-file` to copy a protected environment file into the backup directory without printing secret values. Restore refuses to overwrite an existing database unless `--force` is provided and verifies the backup before copying:

```bash
agentcrawl restore --backup-db ./backups/agentcrawl-YYYYMMDD-HHMMSS.db --db agentcrawl.db --force
```

## Security defaults

The HTTP server rejects local file paths, localhost, private networks, non-HTTP schemes, embedded URL credentials, and redirects to non-global addresses. Local files remain available through the Python library.

Do not expose the API without authentication, TLS, request limits, and network controls. See [SECURITY.md](SECURITY.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Docs you'll actually use 📚

- [Install for agents](INSTALL_FOR_AGENTS.md): canonical setup flow for coding agents.
- [Examples](docs/EXAMPLES.md): copy-paste workflows for CLI, Python, HTTP, MCP, Docker, and agents.
- [Quality benchmarks](docs/QUALITY_BENCHMARKS.md): how extraction quality is measured and reported.
- [Operations](docs/OPERATIONS.md): deployment, backup, restore, and production checks.
- [Release checklist](docs/RELEASE.md): PyPI/GHCR release validation and smoke tests.
- [Comparison](docs/COMPARISON.md): choose between AgentCrawl, Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, and Stagehand.

## Optional LLM extraction

AgentCrawl Community does not require an LLM for scraping, crawling, API, Docker, or MCP usage. The legacy prompt-driven `AgentCrawler.extract()` path is optional: install `agentcrawl-ai[llm]` and configure `llm` or `llm_model` before using it. Built-in web search is disabled by default; keep search in your agent/provider layer unless you explicitly configure a search backend.

## Development

```bash
pip install -e ".[server,mcp,llm,dev]"
pytest -q
ruff check agentcrawl tests examples benchmarks
```

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## License

AgentCrawl Community is licensed under Apache License 2.0. Commercial modules and hosted services are separate products and are not included in this repository.
