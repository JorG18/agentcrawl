# Security Policy

## Reporting

Do not open public issues for suspected vulnerabilities. Until a dedicated security address is published, contact the repository owner privately through the hosting platform.

Include affected versions, reproduction steps, impact, and any suggested mitigation. Do not include scraped private data or active credentials.

## Deployment

- Keep bearer authentication enabled.
- Use long random API keys and rotate exposed keys.
- Remember the read-only dashboard (`/dashboard`, `/api/dashboard/summary`) follows the auth setting. With `AGENTCRAWL_AUTH_ENABLED=true` it requires a key; `AGENTCRAWL_DASHBOARD_PUBLIC=true` only makes sense on a loopback or private-network host.
- Keep `AGENTCRAWL_OWNER_API_KEYS` limited to operators. Owner keys bypass rate limiting and are the only keys that can read jobs created by another key; regular keys only reach their own jobs.
- Put the service behind TLS and network-level request limits.
- Configure `AGENTCRAWL_RATE_LIMIT_PER_MINUTE` for expected traffic.
- Keep `AGENTCRAWL_ALLOW_LOCAL_FILES=false` on network services.
- Keep `AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false` unless the service is isolated and internal access is intentional.
- Treat browser execution as untrusted workload and constrain CPU, memory, and concurrency.
- Update the base image and Python dependencies regularly.

The built-in URL checks reduce SSRF risk but are not a replacement for egress firewall rules. Refusals from those checks (SSRF guard, `airgap=True` allowlist) are deterministic and fail immediately rather than being retried.

Request bodies cannot override server-controlled or privacy-relevant engine settings. A `config` override naming a key the server does not accept (`airgap`, `audit`, `allowlist_domains`, `allow_private_network`, …) is rejected with `400` instead of being silently ignored.
