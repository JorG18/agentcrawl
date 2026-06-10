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
AGENTCRAWL_API_KEYS=<server-key>
AGENTCRAWL_OWNER_API_KEYS=<owner-key>
AGENTCRAWL_DB=/var/lib/agentcrawl/agentcrawl.db
AGENTCRAWL_ALLOW_LOCAL_FILES=false
AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false
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
GET    /v1/usage
GET    /v1/stats
DELETE /v1/cache
```

Large crawls should use a stable `Idempotency-Key`, retain the returned job ID, and poll the same job. Read completed documents with `offset` and `limit`.

## MCP

Local MCP mode does not require an API server:

```bash
agentcrawl mcp
```

Remote API-backed MCP mode uses:

```bash
AGENTCRAWL_BASE_URL=https://agentcrawl.internal.example AGENTCRAWL_API_KEY=<client-key> agentcrawl mcp
```

## Verification

A deployment is accepted only after:

```text
service/container active
/health successful
authenticated /v1/stats successful
scrape https://example.com contains Example Domain
MCP tool discovery works in the intended agent client
```

## Production Checklist

Minimum production checks:

- Authentication and owner keys configured.
- TLS or private-network access enforced.
- Local-file and private-network access disabled unless explicitly needed.
- Persistent database storage and tested backups available.
- Worker, browser, and per-domain concurrency sized for memory and target sites.
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
