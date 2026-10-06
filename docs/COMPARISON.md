# Comparison

AgentCrawl is for teams and builders who want agent-ready web extraction without handing crawler state, cache, retries, and jobs to a hosted scraping API.

Use this page to pick the right tool for the job. These projects overlap, but each one shines in a different workflow.

## Product fit

| Project | Best for | Operational model |
| --- | --- | --- |
| AgentCrawl | Self-hosted Markdown extraction, bounded crawling, MCP access, durable jobs, cache, retries, usage accounting, and API control. | Local library, CLI, HTTP API, Docker, and MCP server. |
| Firecrawl | Hosted web context API with search, scrape, crawl, map, batch jobs, structured output, media parsing, SDKs, and browser actions. | Hosted API with open-source components. |
| Crawl4AI | Deep browser and crawler control for LLM-ready Markdown, sessions, cookies, proxies, hooks, screenshots, and extraction strategies. | Local library, CLI, Docker, and self-hosted API options. |
| ScrapeGraphAI | Prompt-driven structured extraction and graph-style scraping pipelines across websites and local documents. | Python library, hosted API, SDKs, integrations, and MCP options. |
| Scrapling | Python scraping library with browser-fingerprinted HTTP, Playwright and stealth (Patchright) fetchers that can click through Cloudflare Turnstile, persistent sessions, and selectors that relocate elements after a site changes. | Python library, interactive shell, Docker, and MCP server (the agent picks the fetcher and a CSS selector; no main-content detection). |
| Jina Reader | Simple URL-to-LLM-text and search-to-context access through `r.jina.ai` and `s.jina.ai`. | Hosted reader/search API with an open-source branch. |
| Crawlee | General-purpose crawling framework with queues, routing, storage, sessions, proxies, HTTP crawlers, and browser crawlers. | Python/TypeScript crawler framework. |
| Stagehand | Browser automation with natural-language actions, repeatable workflows, and structured browser extraction. | Browser automation SDK. |

## Where AgentCrawl fits ✨

Choose AgentCrawl when you want web extraction that runs from your own environment and plugs directly into agents.

AgentCrawl gives you:

- HTTP-first extraction for fast static-page scraping.
- Optional Playwright or Camofox-backed browser fallback for rendered pages.
- Markdown, text, links, metadata, local document ingestion, and optional structured extraction.
- Bounded site mapping and crawling.
- Durable crawl jobs with checkpointed queues, retries, cancellation, events, and paginated results.
- SQLite-backed cache, usage, jobs, events, and failures.
- Bearer authentication, `robots.txt` support, and SSRF protection for exposed APIs.
- MCP tools for agents that need scrape, map, crawl, job, usage, and cache operations.
- Docker, CLI, Python, HTTP API, and backup/restore workflows.

## When another tool may fit better

Use Firecrawl when you want a hosted API with broad web-scale infrastructure, media parsing, browser actions, and SDK coverage already packaged around the service.

Use Crawl4AI when your workflow needs fine-grained browser automation controls, custom hooks, proxy/session handling, screenshots, or advanced local crawling strategies.

Use ScrapeGraphAI when your main workflow is prompt-driven structured extraction through LLM pipeline graphs, especially across mixed web and local document sources.

Use Scrapling when you write the scraper yourself in Python and want low-level fetcher control, stealth browsing with a Turnstile click, and selectors that survive layout changes.

Use Jina Reader when you want the simplest possible hosted URL-to-text or search-to-context endpoint.

Use Crawlee when you are building a custom crawler application and want a framework with routing, request queues, datasets, session pools, and proxy controls.

Use Stagehand when the main task is operating a browser through natural-language and code actions rather than running a crawler service.

## Current product boundary

Community is everything that runs on your own machine: the engine, a local browser that presents itself as the Chrome it is (with an optional Patchright retry), proxies you bring and your own LLM. It speaks Firecrawl's v2 API as well as its own, so Firecrawl code can move to a self-hosted server by changing the base URL. It never returns a challenge page as content.

Managed proxies, geolocation, a managed browser fleet, CAPTCHA solving, schedules, webhooks and retained datasets belong to AgentCrawl Enhanced, a planned hosted API.

Community ships the agent-facing basics: search, llms.txt, citable chunks, bounded browser actions and screenshots, JavaScript rendering with a local browser, adaptive crawls, change checks, Office/PDF documents, CSS and schema extraction, a TypeScript client and LangChain/LlamaIndex loaders. Its output is checked against real public sites on every engine change (the live smoke workflow), and bot challenges are reported with the signals that identified them.

That boundary is intentional: AgentCrawl focuses first on agent-facing extraction that remains under user control.
