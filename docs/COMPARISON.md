# Comparison

AgentCrawl is built for agents that need self-hosted web extraction with a small operational surface. It is not trying to replace every crawler, browser automation framework, or hosted scraping API.

This page explains where AgentCrawl fits today and what still needs to improve.

## Positioning

| Project | Strongest fit | Tradeoff |
| --- | --- | --- |
| AgentCrawl | Self-hosted Markdown extraction, MCP tools, durable crawl jobs, cache, retries, and API control in your own environment. | Early project; fewer public benchmarks, examples, SDKs, and browser workflow features than mature alternatives. |
| Firecrawl | Hosted web context API with search, scrape, crawl, map, batch jobs, structured output, media parsing, actions, SDKs, and public scale claims. | Hosted-first workflow; self-hosted users still inherit a larger service surface. |
| Crawl4AI | Deep browser and crawler control for LLM-ready Markdown, sessions, cookies, proxies, hooks, screenshots, and advanced extraction strategies. | More configuration surface; less focused on a small self-hosted agent service with durable API jobs. |
| ScrapeGraphAI | Prompt-driven structured extraction and graph-style scraping pipelines across web pages and local documents. | LLM-pipeline centered; less focused on persistent crawl job operations. |
| Jina Reader | Extremely simple URL-to-LLM-text and search-to-context API through `r.jina.ai` and `s.jina.ai`. | Hosted API model; less control over local persistence, jobs, cache, and server policy. |
| Crawlee | General-purpose crawling framework with queues, storage, routing, sessions, proxies, browser and HTTP crawlers. | Framework for building crawlers; AgentCrawl aims to be a ready agent-facing extraction service. |
| Stagehand | Browser automation with natural-language actions and structured extraction. | Browser workflow framework; not a dedicated crawler/cache/MCP extraction service. |

## What AgentCrawl Already Prioritizes

- HTTP-first extraction so common pages do not require a browser runtime.
- Optional Playwright or Camofox-backed browser fallback for pages that need rendering.
- Markdown, text, links, metadata, and optional structured extraction.
- Bounded site mapping and crawling.
- Durable crawl jobs with checkpointed queues, retries, cancellation, events, and paginated results.
- SQLite-backed cache, usage, jobs, events, and failures.
- Bearer authentication, `robots.txt` support, and SSRF protection for exposed APIs.
- MCP tools for direct agent use.
- Docker, CLI, Python, HTTP API, and backup/restore workflows.

## Current Gaps

These gaps are intentional to state publicly. They should be closed with implementation and reproducible tests before stronger claims appear in the README.

### Extraction Quality Evidence

AgentCrawl needs a public fixture corpus and repeatable quality report covering documentation sites, news, ecommerce pages, blogs, forums, tables, code blocks, JS-heavy pages, redirects, and failure cases.

Until that exists, quality claims should stay specific and conservative.

### Document Ingestion

PDF ingestion is the first missing document capability. Office formats, plain text, XML, JSON, Markdown input normalization, and OCR can follow after PDF support is stable.

### Browser Workflows

AgentCrawl supports browser fallback, but the public API should grow bounded browser actions before competing with Firecrawl, Crawl4AI, or Stagehand on interactive pages:

- wait;
- click;
- scroll;
- type;
- screenshot or capture;
- cookies and session reuse;
- clear browser cleanup guarantees.

### SDKs And Examples

Python, CLI, HTTP, Docker, and MCP are enough for the first release. Adoption will improve with:

- TypeScript examples or a small Node SDK;
- complete OpenAPI examples;
- cookbook examples for agent clients;
- public demos for docs sites, ecommerce pages, and structured extraction.

### Distribution Signals

The repo should expose the trust signals users expect before adopting a new scraping tool:

- PyPI release;
- container image;
- badges for CI, package version, and license;
- release notes;
- issue templates;
- benchmark report;
- comparison page;
- public examples.

## Near-Term Differentiator

AgentCrawl should compete first as:

> Firecrawl-style self-hosted extraction for agents, with durable jobs and MCP, without forcing a hosted SaaS path.

That is narrower than the full market, but it is defensible. The strongest first users are developers running local agents, private agent stacks, VPS deployments, and internal tools that need web context without sending crawl state through a third-party hosted API.
