# AgentCrawl Operations

This is the public deployment, connection, and production-operations guide. Never commit or print API keys.

## Deployment Options

### Published Docker image

Use the GHCR image when you want the fastest self-hosted API deployment:

```bash
docker run --rm -p 8000:8000 -e AGENTCRAWL_API_KEYS=<server-key> ghcr.io/jorg18/agentcrawl:latest
curl http://127.0.0.1:8000/health
```

For persistent state, mount `/data`:

```bash
docker volume create agentcrawl-data
docker run -d --name agentcrawl -p 8000:8000 -e AGENTCRAWL_API_KEYS=<server-key> -v agentcrawl-data:/data ghcr.io/jorg18/agentcrawl:latest
```

The default image is HTTP-first and does not include a browser runtime.

### Docker Compose from a checkout

Use Compose when developing from the repository or when you want to customize the local build:

```bash
cp .env.example .env
# Replace all example values before exposure.
docker compose up -d
curl http://127.0.0.1:8000/health
```

Use `docker compose up --build -d` only when you intentionally want to rebuild the local image from the checkout.

Use long random values for server keys and client credentials. Keep the SQLite volume persistent.

### Systemd

Run AgentCrawl from a dedicated virtual environment with an environment file:

```text
AGENTCRAWL_AUTH_ENABLED=true
AGENTCRAWL_API_KEYS=<server-key>,<owner-key>
# Owner keys are an elevation of a key that is already accepted by
# AGENTCRAWL_API_KEYS: authentication runs first, so a key listed only here is
# rejected with 403. List every owner key in both variables.
AGENTCRAWL_OWNER_API_KEYS=<owner-key>
AGENTCRAWL_DB=/var/lib/agentcrawl/agentcrawl.db
AGENTCRAWL_ALLOW_LOCAL_FILES=false
AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false
# Keep the dashboard behind auth on an exposed host.
AGENTCRAWL_DASHBOARD_PUBLIC=false
```

Run Uvicorn behind Tailscale or a TLS reverse proxy with network request limits.

## Production Systemd Pattern

Use placeholders in shared docs and keep hostnames, private IPs, SSH aliases, and real credential paths in private runbooks only.

```text
SSH alias:       <your-host-alias>
Project:         /opt/agentcrawl
Service:         agentcrawl.service
Database:        /var/lib/agentcrawl/agentcrawl.db
Environment:     /etc/agentcrawl.env
Loopback API:    http://127.0.0.1:8000
Private API:     https://agentcrawl.internal.example
MCP launcher:    /opt/agentcrawl/run-mcp.sh
```

Operations:

```bash
ssh <your-host-alias> "systemctl status agentcrawl --no-pager -l"
ssh <your-host-alias> "systemctl restart agentcrawl"
ssh <your-host-alias> "journalctl -u agentcrawl -f"
ssh <your-host-alias> "curl -fsS http://127.0.0.1:8000/health"
```

Before production changes, back up code, the environment file, and SQLite with its online backup mechanism. Store backups outside the application directory.

```bash
agentcrawl backup --db /var/lib/agentcrawl/agentcrawl.db --env-file /etc/agentcrawl.env --output-dir /secure/backups/agentcrawl
```

The command uses SQLite online backup, runs `pragma integrity_check` on the copy, writes a manifest, and never prints environment file contents. Restore a verified backup only while the service is stopped:

```bash
systemctl stop agentcrawl
agentcrawl restore --backup-db /secure/backups/agentcrawl/agentcrawl-YYYYMMDD-HHMMSS.db --db /var/lib/agentcrawl/agentcrawl.db --force
systemctl start agentcrawl
```

## HTTP And MCP Connection

Remote clients set:

```bash
export AGENTCRAWL_BASE_URL=https://agentcrawl.internal.example
export AGENTCRAWL_API_KEY=<client-key>
agentcrawl mcp
```

Main endpoints. `/health` is unauthenticated; the dashboard endpoints follow `AGENTCRAWL_AUTH_ENABLED` unless `AGENTCRAWL_DASHBOARD_PUBLIC=true`; everything else requires `authorization: Bearer <key>`.

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
GET    /v1/usage
GET    /v1/stats
DELETE /v1/cache
```

Large crawls should use a stable `Idempotency-Key`, retain the returned job ID, and poll the same job. Read completed documents with `offset` and `limit`.

Job state, the scrape cache, and the aggregate stats are scoped per key: a regular key can only read, cancel, or retry its own jobs, only lists its own crawl failures, and only sees (and clears) its own cache rows. Keys listed in `AGENTCRAWL_OWNER_API_KEYS` are the operator keys — they bypass rate limiting and can read every job and the global aggregates, so issue them deliberately. An owner key must **also** appear in `AGENTCRAWL_API_KEYS`: authentication runs before the owner check, so a key listed only under the owner variable is refused with `403`.

## MCP

Local MCP mode does not require an API server:

```bash
agentcrawl mcp
```

Local mode (MCP and the CLI without `--remote`) and the server read the same engine variables (fetcher, browser backend and engine, CDP URL, Camofox URL and key, user agent, timeouts and page budget, retries, crawl defaults, private network, robots, browser fallback, airgap, audit, OCR). The server keeps its own default for `AGENTCRAWL_BROWSER_STRICT_NETWORK` (on), and ignores the `cdp` engine and `AGENTCRAWL_CDP_URL`: attaching to your own Chrome is local mode only. CLI flags override them per command.

Browser sessions (0.4.5): a request cannot choose a saved login. The operator can run the whole server as one with `AGENTCRAWL_BROWSER_SESSION=<name>` (created with `agentcrawl login` on the host; files live in `AGENTCRAWL_SESSIONS_DIR`, default `~/.agentcrawl/sessions`, mode 0600). Every API key then reads pages as that user, so only do this on a server whose keys you all trust. `browser_iframes` and `browser_shadow_dom` (default on) are per-request overrides.

Per-request API overrides added in 0.3.0: `browser_actions` (bounded steps run before the page is read; needs the Playwright backend in the image) and `ocr` (image-only PDF pages; needs the docs extra and Tesseract). `POST /v1/crawl` also accepts `query` and `stop_after_irrelevant` for adaptive crawls.

Remote API-backed MCP mode uses:

```bash
AGENTCRAWL_BASE_URL=https://agentcrawl.internal.example AGENTCRAWL_API_KEY=<client-key> agentcrawl mcp
```

## Request Limits And Usage Metering

| Setting | Default | Effect |
| --- | --- | --- |
| `AGENTCRAWL_BROWSER_CONCURRENCY` | `4` | Browsers kept open per engine (each serves one page at a time). With the `[stealth]` extra, Patchright retries have their own pool, so up to twice this many can be open; size memory for that. With `cloak`, lower it to 1 if your CloakBrowser build allows one session at a time. |
| `AGENTCRAWL_BROWSER_ENGINE` | `playwright` | `patchright` (`[stealth]` extra), `cloak` (`[cloak]` extra plus `python -m cloakbrowser install`) or `cdp` (attach to your own Chrome, see `AGENTCRAWL_CDP_URL`; local mode only, the server ignores it) for every browser page. |
| `AGENTCRAWL_CDP_URL` | `http://127.0.0.1:9222` | Chrome to attach to with `AGENTCRAWL_BROWSER_ENGINE=cdp` (started with `--remote-debugging-port`). Loopback only. |
| `AGENTCRAWL_LLM_MAX_PAGES` | `20` | Most pages one call may send to the operator's LLM for `formats=["json"]` or `["summary"]` (`/v1/scrape_many`, `/v2/batch/scrape`, MCP `scrape_many`); a bigger batch is refused before any call. The model is `AGENTCRAWL_LLM_MODEL`; a request never chooses it. |
| `AGENTCRAWL_PAGE_BUDGET_MS` | `45000` | One time budget per page covering every step (HTTP, browser, retries). A page that runs out of it fails with `error_type: "timeout"` and a `next_step`. `0` turns it off. |
| `AGENTCRAWL_RATE_LIMIT_PER_MINUTE` | `60` | Units per key per minute. A request costs 1; `/v1/scrape_many` costs 1 per URL, `/v1/search` 1 plus 1 per result page when it scrapes, and a `wait=true` crawl 1 per page (capped at the window). Owner keys are exempt. |
| `AGENTCRAWL_SYNC_CRAWL_MAX_PAGES` | `25` | Largest `wait=true` crawl; bigger crawls must run as durable jobs. |
| `AGENTCRAWL_MAX_REQUEST_BYTES` | `1000000` | Largest request body (by `Content-Length`); larger ones get `413` before authentication, and a chunked body streaming past it is cut off with `400`. Request `config` overrides are also capped: `crawl_max_pages` 10,000, `max_response_bytes` 50 MB, `timeout_ms` and `page_budget_ms` 5 min, `browser_wait_ms` 60 s, `http_retry_delay` 60 s, `crawl_retry_delay` and `crawl_retry_max_delay` 60 s, `http_retries` and `crawl_url_retries` 5; `page_budget_ms` 0 (no budget) is refused. At most 20 `include`/`exclude` patterns, each match limited to 50 ms; a pattern that backtracks past it excludes the URL instead of stalling the server. |
| `AGENTCRAWL_SCRAPE_MANY_CONCURRENCY` | `8` | Parallel pages per `/v1/scrape_many` call; per-domain limits still apply. |
| `AGENTCRAWL_SEARCH_ENGINE` | `none` | Enables `/v1/search`, MCP `search_web` and CLI `search`: `serper` with `SERPER_API_KEY`, or `duckduckgo` (no key, but it often answers automated clients with a bot check, reported as an error). Queries leave the host for that engine, so it is off by default and a request cannot choose it. |
| `AGENTCRAWL_TIMEOUT_MS` | `30000` | Per socket operation. A whole body must also arrive within 3 × this value. |

Other engine and server variables (local mode and the server read the engine ones alike):

| Setting | Default | Effect |
| --- | --- | --- |
| `AGENTCRAWL_AIRGAP` | `false` | Refuse every host outside the target and `AGENTCRAWL_AIRGAP_ALLOWLIST`; also forces strict browser networking and refuses Camofox. |
| `AGENTCRAWL_AIRGAP_ALLOWLIST` | empty | Comma-separated extra hosts allowed under airgap (empty = the target host only). |
| `AGENTCRAWL_AUDIT` | `false` | Record every request made for a page in `metadata.audit_trail`; forces strict browser networking. |
| `AGENTCRAWL_HTTP_RETRIES` / `AGENTCRAWL_HTTP_RETRY_DELAY` | `2` / `1.0` | Retries of a failed HTTP fetch and the first delay in seconds. |
| `AGENTCRAWL_CRAWL_URL_RETRIES` | `2` | Retries of a failed page inside a crawl (API cap 5). |
| `AGENTCRAWL_CRAWL_RETRY_DELAY` / `AGENTCRAWL_CRAWL_RETRY_MAX_DELAY` | `2.0` / `60.0` | First and largest delay, in seconds, between those retries. |
| `AGENTCRAWL_CAMOFOX_USER_ID` | `agentcrawl` | User id AgentCrawl sends to the Camofox service. |
| `AGENTCRAWL_LLM_PROVIDER` | unset | Prefix for `AGENTCRAWL_LLM_MODEL` when the model name has no `provider:` part. |
| `AGENTCRAWL_MCP_TIMEOUT` | `120` | Seconds the MCP server waits for the remote API when `AGENTCRAWL_BASE_URL` is set. |
| `AGENTCRAWL_INSTANCE_ID` | `pid-<pid>-<random>` | Name this server process uses as owner of job leases; set it only to tell processes apart in the database. |
| `AGENTCRAWL_SCHEDULE_LEASE_SECONDS` | `300` | Length of a job's lease, for scheduling and for its run (a live run renews it). A process that dies holds its jobs until the lease expires; then the 60 s sweep requeues them. |

`usage_events` rows (`GET /v1/usage`, `GET /v1/stats` → `usage_by_endpoint`) are the metering record:

- `/v1/scrape`, `/v1/scrape.cache_hit`, `/v1/map`, `/v1/extract`, `/v1/extract_css`: 1 unit per call (per URL for `scrape_many`).
- `/v1/search`: 1 unit per search; each scraped result page is also metered as `/v1/scrape`.
- `/v1/crawl`: 1 unit per document produced.
- `/v1/extract.llm_calls`: model requests made by an extraction, reattempts included.
- `/v1/jobs.failures.retry` and the re-run it triggers are billed to the **job owner**, whoever pressed retry.

With `uvicorn --workers > 1` (or several servers on one database), restart recovery only requeues jobs whose run lease expired, so a live worker keeps its job. Two limits stay per process: `AGENTCRAWL_RATE_LIMIT_PER_MINUTE` and the per-domain concurrency and delay are counted in each process, so N processes allow up to N times as much. One process with `AGENTCRAWL_WORKERS` threads keeps them exact. Restart every process sharing the database together when you upgrade.

## Verification

A deployment is accepted only after:

```text
service/container active
/health successful
authenticated /v1/stats successful
scrape https://docs.python.org/3/library/json.html contains json
MCP tool discovery works in the intended agent client
```

## Production Checklist

Minimum production checks:

- Authentication and owner keys configured.
- TLS or private-network access enforced.
- Local-file and private-network access disabled unless explicitly needed.
- Persistent database storage and tested backups available.
- Worker, browser, and per-domain concurrency sized for memory and target sites.
- Each crawl job fetches 2 pages at a time (fixed on the server; `crawl_concurrency` is a library setting), so page fetches in flight are up to twice the number of job workers; `AGENTCRAWL_DOMAIN_MAX_CONCURRENCY` still caps the requests to one domain across all jobs.
- Logs, health checks, database integrity, and disk usage monitored.
- Restore and rollback procedure tested before public launch.

## Version Control And Recovery

Use feature branches for risky work, keep `main` releasable, and create checkpoint tags before large migrations or deployment changes:

```bash
git status --short
git switch -c feature/short-description
git tag -s checkpoint-YYYY-MM-DD-description -m "Known-good checkpoint"
git push origin --tags
```

Do not use `git reset --hard` or force-push shared branches as routine recovery.
