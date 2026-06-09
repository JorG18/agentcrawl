# AgentCrawl

[![CI](https://github.com/JorG18/agentcrawl/actions/workflows/ci.yml/badge.svg)](https://github.com/JorG18/agentcrawl/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

![AgentCrawl README hero](assets/readme-hero.png)

🕷️ Self-hosted web extraction for AI agents.

AgentCrawl turns messy web pages into clean Markdown, text, links, metadata, and structured data that agents can actually use. Run it from Python, the CLI, an HTTP API, Docker, or MCP. Your crawler, cache, retries, jobs, and extracted data stay in your environment.

## Why AgentCrawl? ✨

Agents need fresh web context, but raw HTML is noisy and one-off scraper scripts age badly. AgentCrawl gives them a reliable extraction layer with the operational pieces already built in:

- 🎯 Known URL or local document in, clean Markdown out with main-content extraction, table preservation, and fenced code blocks.
- ⚡ Fast HTTP extraction first; browser rendering only when a page really needs it.
- 🧱 Durable crawl jobs with checkpoints, retries, pagination, cancellation, events, and failure inspection.
- 🗄️ SQLite-backed cache, usage, jobs, events, and crawl failures.
- 🔐 Bearer auth, `robots.txt` support, SSRF protections, unsafe redirect blocking, and private-network controls for exposed APIs.
- 🤖 MCP tools for scraping, mapping, crawling, jobs, usage, and cache control.

## Quick start 🚀

Python 3.10 or newer is required.

```bash
git clone https://github.com/JorG18/agentcrawl.git
cd agentcrawl
python -m pip install -e "."
agentcrawl scrape https://example.com
```

For MCP:

```bash
python -m pip install -e ".[mcp]"
agentcrawl doctor
agentcrawl mcp
```

The base package uses HTTP extraction and does not install a browser. Add browser rendering only when a site needs JavaScript:

```bash
python -m pip install -e ".[browser]"
playwright install chromium
```

Local Markdown, text, JSON, and XML files work in the base package. Add PDF ingestion only when needed:

```bash
python -m pip install -e ".[docs]"
agentcrawl scrape ./document.pdf
```

AgentCrawl also supports an optional external Camofox REST backend:

```bash
export AGENTCRAWL_BROWSER_BACKEND=camofox
export AGENTCRAWL_CAMOFOX_URL=http://127.0.0.1:9377
export AGENTCRAWL_CAMOFOX_ACCESS_KEY=replace-if-access-control-is-enabled
```

## Docs you'll actually use 📚

- [Comparison](docs/COMPARISON.md): choose between AgentCrawl, Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, and Stagehand.
- [Examples](docs/EXAMPLES.md): copy-paste workflows for CLI, Python, HTTP, MCP, Docker, and agents.
- [Quality benchmarks](docs/QUALITY_BENCHMARKS.md): how extraction quality is measured and reported.
- [Operations](docs/OPERATIONS.md): deployment, backup, restore, and production checks.
- [Release checklist](docs/RELEASE.md): PyPI/GHCR release validation and smoke tests.
- [Install for agents](INSTALL_FOR_AGENTS.md): the canonical setup flow for coding agents.

## Python 🐍

```python
from agentcrawl import AgentCrawl

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("https://example.com")

print(document.markdown)
print(document.links)
```

Structured extraction can use a local callable or a supported model provider through `AgentCrawler`.

## Extraction quality 🧹

The Community engine focuses on stable, agent-ready Markdown before benchmark claims:

- selects semantic content from `<main>`, `<article>`, documentation/content containers, or text-rich fallback blocks;
- removes unsafe and noisy page chrome such as scripts, styles, hidden content, nav, footer, cookie banners, sidebars, and related-post blocks;
- preserves Markdown tables with headers and cell values;
- preserves fenced code blocks and language tags from common classes such as `language-python` and `lang-javascript`.

## Local documents 📄

Community supports local document ingestion without sending file contents to a hosted parser:

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
python -m pip install -e ".[docs]"
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

## HTTP API 🌐

Authentication is enabled by default. Configure at least one API key before exposing the server:

```bash
export AGENTCRAWL_API_KEYS="replace-with-a-long-random-key"
python -m pip install -e ".[server]"
agentcrawl serve --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Scrape a URL:

```bash
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer replace-with-api-key" \
  -H "content-type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown","links","metadata"]}'
```

Main endpoints:

```text
GET    /health
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

## Crawl jobs 🧭

Start an asynchronous crawl:

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
```

HTTP clients can attach an idempotency key so retries return the original job instead of starting a duplicate:

```bash
curl http://127.0.0.1:8000/v1/crawl \
  -H "authorization: Bearer replace-with-api-key" \
  -H "content-type: application/json" \
  -H "Idempotency-Key: docs-crawl-2026-06-06" \
  -d '{"url":"https://example.com","max_pages":25,"max_depth":2}'
```

Running jobs checkpoint their queue, visited URLs, retry attempts, progress, and extracted documents in SQLite. Transient page failures use persisted exponential backoff without occupying a crawl worker. They are reclaimed after a service restart.

Read completed documents page by page:

```bash
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

Inspect or cancel a job:

```bash
agentcrawl --remote job JOB_ID
agentcrawl --remote job-cancel JOB_ID
```

`/v1/stats` reports queue readiness, delayed retries, running and cancelling jobs, crawl failures by status, open retryable failures, and open failures by error type.

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

## MCP 🤖

```bash
# Local HTTP scraping, no environment variables needed.
agentcrawl mcp

# Remote API mode uses AGENTCRAWL_BASE_URL and AGENTCRAWL_API_KEY.
```

MCP tools cover scraping, mapping, crawling, job status, cancellation, failure inspection, selective failure retries, usage, cache statistics, and cache clearing. Coding agents should follow [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md).

## Docker 🐳

```bash
cp .env.example .env
# Replace AGENTCRAWL_API_KEYS and AGENTCRAWL_API_KEY in .env
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

After the public image is published, the expected image path is:

```bash
docker pull ghcr.io/jorg18/agentcrawl:latest
```

The test suite validates the Dockerfile, Compose hardening, persistent `/data` volume, healthcheck, OCI labels, and required `.env.example` keys. A Docker daemon is still required for the final image build smoke test.

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

## Development

```bash
python -m pip install -e ".[server,mcp,llm,dev]"
pytest -q
ruff check agentcrawl tests examples
```

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## License

AgentCrawl Community is licensed under Apache License 2.0. Commercial modules and hosted services are separate products and are not included in this repository.
