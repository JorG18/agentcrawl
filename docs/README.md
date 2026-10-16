# AgentCrawl docs

Start here when you need deeper documentation beyond the root README.

## Core docs

| File | Use when |
| --- | --- |
| [EXAMPLES.md](EXAMPLES.md) | You want copy-paste workflows for CLI, Python, HTTP API, Docker, MCP, browser rendering, and local documents. |
| [BENCHMARKS.md](BENCHMARKS.md) | You want to know how AgentCrawl compares with Crawl4AI, Scrapling and Firecrawl on real websites, and how to reproduce it. |
| [QUALITY_BENCHMARKS.md](QUALITY_BENCHMARKS.md) | You want to understand the checked-in extraction quality suite and benchmark policy. |
| [OPERATIONS.md](OPERATIONS.md) | You run AgentCrawl as a service and need health checks, backup, restore, and production notes. |
| [RELEASE.md](RELEASE.md) | You are preparing a PyPI, GitHub, or GHCR release. |
| [COMPARISON.md](COMPARISON.md) | You need public positioning against adjacent tools without unsupported superiority claims. |

## Boundary rule

Public docs should use `https://docs.python.org/3/library/json.html` or another page that reads over plain HTTP for normal smoke tests (PyPI now answers plain HTTP with a client challenge and needs the browser extra).

`https://example.com` is the documented Community boundary case because it often returns a Cloudflare client challenge. Use it only when explaining honest `client_challenge` behavior, not as the default success example.
