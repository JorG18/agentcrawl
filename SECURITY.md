# Security Policy

## Reporting

Do not open public issues for suspected vulnerabilities. Until a dedicated security address is published, contact the repository owner privately through the hosting platform.

Include affected versions, reproduction steps, impact, and any suggested mitigation. Do not include scraped private data or active credentials.

## Deployment

- Keep bearer authentication enabled.
- Use long random API keys and rotate exposed keys.
- Remember the read-only dashboard (`/dashboard`, `/api/dashboard/summary`) follows the auth setting. With `AGENTCRAWL_AUTH_ENABLED=true` it requires a key; `AGENTCRAWL_DASHBOARD_PUBLIC=true` only makes sense on a loopback or private-network host.
- Keep `AGENTCRAWL_OWNER_API_KEYS` limited to operators. Owner keys bypass rate limiting and are the only keys that can read jobs created by another key; regular keys only reach their own jobs.
- Put the service behind TLS and network-level request limits. The server itself refuses request bodies over `AGENTCRAWL_MAX_REQUEST_BYTES` (1 MB) before authentication: by `Content-Length` (413), or cut off once a chunked body streams past it (400).
- Configure `AGENTCRAWL_RATE_LIMIT_PER_MINUTE` for expected traffic.
- Keep `AGENTCRAWL_ALLOW_LOCAL_FILES=false` on network services. The same variable gates the local MCP engine, which refuses file paths unless it is set; when you enable it, also set `AGENTCRAWL_LOCAL_FILES_ROOT` (enforced with real paths, so `..` and escaping symlinks are refused).
- Keep `AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false` unless the service is isolated and internal access is intentional.
- Treat browser execution as untrusted workload and constrain CPU, memory, and concurrency.
- Update the base image and Python dependencies regularly.

The built-in URL checks reduce SSRF risk but are not a replacement for egress firewall rules. What they cover:

- **HTTP fetcher, search and robots/sitemap discovery:** every hop is validated and, unless `allow_private_network` is on, the connection is DNS-pinned (resolved once, validated, connected to that address) so DNS rebinding cannot swap in a private address between check and connect. Through an `HTTP(S)_PROXY` from the environment the proxy resolves the host, so there is no pinning and `metadata.dns_pinned` is `false`. IPv6 addresses that wrap an IPv4 one (NAT64 `64:ff9b::/96`, 6to4 `2002::/16`, `::a.b.c.d`) are checked by the IPv4 address inside; local-use NAT64 (`64:ff9b:1::/48`) is refused.
- **Playwright fetcher:** every browser request — iframes, sub-resources, `fetch()`/XHR, WebSockets — is checked against the SSRF guard and the airgap allowlist before it is sent; service workers are blocked. Redirect hops are checked before they are sent only with `browser_strict_network=true` (the server's default). In the library's default mode Chromium follows a sub-resource's or iframe's redirects itself: the page's own redirect chain is checked after loading, and the page or an iframe whose URL is refused when its HTML is read is never returned, but the request to the redirect target has already been made. The browser resolves DNS itself, so this path has **no DNS pinning**.
- **Camofox backend:** its browser runs in a separate service and **cannot** be guarded from AgentCrawl. It refuses to run under `airgap=True`, and the API only uses it when the operator configured it. Run it on an isolated network if pages are untrusted.
- **Caller-supplied regexes** (`include`/`exclude`, CSS-schema `regex` fields) run on Python's `re`, which has no match timeout: validation rejects invalid patterns but cannot rule out a pathological one. Refusals from those checks (SSRF guard, `airgap=True` allowlist) are deterministic and fail immediately rather than being retried.

Request bodies cannot override server-controlled or privacy-relevant engine settings. A `config` override naming a key the server does not accept (`airgap`, `audit`, `allowlist_domains`, `allow_private_network`, …) is rejected with `400` instead of being silently ignored.
