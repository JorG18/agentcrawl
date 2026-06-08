# AgentCrawl Community

AgentCrawl Community is a self-hosted web extraction service for AI agents. It turns web pages into clean Markdown, text, links, metadata, and optional structured data through a Python API, HTTP API, CLI, or MCP server.

## Features

- HTTP-first scraping with automatic Playwright fallback.
- Main-content extraction with tables, lists, links, and code blocks.
- Site mapping and bounded same-domain crawling.
- Durable crawl jobs with checkpoints, restart resume, persistent retries, pagination, and cancellation.
- Server-side SQLite cache with per-request bypass and TTL.
- Bearer authentication and usage accounting.
- MCP tools and a remote Python client.
- `robots.txt` support enabled by default.
- SSRF protection for public server deployments.
- Local library mode without a hosted subscription.

## Community Scope

Community includes the local engine, single-node API, SQLite persistence, CLI, MCP bridge, and Docker deployment.

See [ROADMAP.md](ROADMAP.md) for current focus areas and planned improvements. Coding agents should follow [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md).

## Quick Install

Python 3.10 or newer is required.

```bash
python -m pip install agentcrawl
agentcrawl scrape https://example.com
```

For an AI client that supports MCP:

```bash
python -m pip install "agentcrawl[mcp]"
agentcrawl doctor
```

The default HTTP fetcher does not require Chromium. Add browser rendering only when needed. AgentCrawl supports Playwright directly and an optional external Camofox REST backend for anti-detection browsing:

```bash
python -m pip install "agentcrawl[browser]"
playwright install chromium

# Optional: point browser fallback at a separately deployed Camofox server.
export AGENTCRAWL_BROWSER_BACKEND=camofox
export AGENTCRAWL_CAMOFOX_URL=http://127.0.0.1:9377
export AGENTCRAWL_CAMOFOX_ACCESS_KEY=replace-if-access-control-is-enabled
```

From a repository checkout, replace `agentcrawl` with `-e .`, for example
`python -m pip install -e ".[mcp]"`.

## Local Python

```python
from agentcrawl import AgentCrawl

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("https://example.com")

print(document.markdown)
print(document.links)
```

Structured extraction can use a local callable or a supported model provider through `AgentCrawler`.

## Run The API

Authentication is enabled by default. Configure at least one API key:

```bash
export AGENTCRAWL_API_KEYS="replace-with-a-long-random-key"
python -m pip install "agentcrawl[server]"
agentcrawl serve --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Scrape:

```bash
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer $AGENTCRAWL_API_KEYS" \
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

Interactive OpenAPI documentation is available at `/docs`.

## Cache

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

## Crawl Jobs

Start an asynchronous crawl:

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
```

HTTP clients can attach an idempotency key so retries return the original job instead
of starting a duplicate:

```bash
curl http://127.0.0.1:8000/v1/crawl \
  -H "authorization: Bearer $AGENTCRAWL_API_KEY" \
  -H "content-type: application/json" \
  -H "Idempotency-Key: docs-crawl-2026-06-06" \
  -d '{"url":"https://example.com","max_pages":25,"max_depth":2}'
```

Running jobs checkpoint their pending queue, visited URLs, retry attempts, progress,
and extracted documents in SQLite. Transient page failures use persisted exponential
backoff without occupying a crawl worker. They are reclaimed automatically after a
service restart.

Completed documents are paginated when reading a job:

```bash
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

The response includes `result.pagination` with `total`, `returned`, and `has_more`.
A `queued` job with a future `available_at` is waiting for a retry and should be
polled using the same job ID.

Inspect or cancel it:

```bash
agentcrawl --remote job JOB_ID
agentcrawl --remote job-cancel JOB_ID
```

Progress contains visited, pending, failed, discovered, cancelled, and, when
applicable, retry count and next retry time. `/v1/stats` includes crawl queue
readiness, delayed retries, running/cancelling jobs, crawl failure counts by
status, open retryable failure count, and open failure counts by error type.


The server schedules asynchronous crawls through a FIFO worker queue. Large jobs
process a configurable page quantum and then return to the end of the queue so
smaller jobs are not starved. Concurrent fetches to one domain are limited
separately from total workers:

```bash
export AGENTCRAWL_WORKERS=4
export AGENTCRAWL_CRAWL_JOB_PAGE_QUANTUM=5
export AGENTCRAWL_DOMAIN_MAX_CONCURRENCY=2
```

Set `AGENTCRAWL_OWNER_API_KEYS` to a comma-separated subset of
`AGENTCRAWL_API_KEYS` to exempt trusted owner keys from API rate limits. Domain
concurrency and crawl politeness still apply because they protect external sites.

## MCP

```bash
# No environment variables are needed for local HTTP scraping.
agentcrawl mcp

# For a remote AgentCrawl API, set AGENTCRAWL_BASE_URL and AGENTCRAWL_API_KEY.
```

Tools include scraping, mapping, crawling, job status, cancellation, failure inspection, selective failure retries, usage, cache statistics, and cache clearing.

## Docker

```bash
cp .env.example .env
# Replace AGENTCRAWL_API_KEYS and AGENTCRAWL_API_KEY in .env
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

The test suite statically validates the Dockerfile, Compose hardening, persistent
`/data` volume, healthcheck, and required `.env.example` keys. A real Docker daemon
is still required for the final image build smoke test.

## Backups

Use SQLite online backup before deployment or migration:

```bash
agentcrawl backup --db agentcrawl.db --output-dir ./backups
```

Pass `--env-file` to copy a protected environment file into the backup directory
without printing secret values. Restore refuses to overwrite an existing database
unless `--force` is provided and verifies the backup before copying:

```bash
agentcrawl restore --backup-db ./backups/agentcrawl-YYYYMMDD-HHMMSS.db --db agentcrawl.db --force
```

## Security Defaults

The HTTP server rejects local file paths, localhost, private networks, non-HTTP schemes, embedded URL credentials, and redirects to non-global addresses. Local files remain available through the Python library.

Do not expose the API without authentication, TLS, request limits, and appropriate network controls. See [SECURITY.md](SECURITY.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Development

```bash
pip install -e ".[server,mcp,llm,dev]"
pytest -q
ruff check agentcrawl tests examples
```

## License

AgentCrawl Community is licensed under Apache License 2.0. Commercial modules and hosted services are separate products and are not included in this repository.
