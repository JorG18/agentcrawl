# AgentCrawl product marketing context

## Product

AgentCrawl is self-hosted web extraction infrastructure for AI agents. It turns URLs and bounded websites into Markdown, text, links, metadata, and optional structured data through Python, CLI, HTTP API, Docker, and MCP.

## Primary audience

- Developers building AI agents that need reliable web inputs.
- Teams that want web-to-Markdown extraction without sending every page through a hosted scraping vendor.
- Operators who need crawls to survive restarts, expose job state, retry failures, and keep data in their own environment.
- Agent framework users who want an MCP server for scraping, mapping, crawling, cache, usage, jobs, and failure inspection.

## Job to be done

When an agent needs to read or crawl a website, AgentCrawl gives it a controlled, local-first extraction layer instead of a one-off scraper script or a hosted black box.

## Core pain points

- LLMs need clean web context, but raw HTML is noisy.
- Browser scraping is expensive and fragile when used by default.
- One-off crawlers lose state, duplicate work, and fail badly after restarts.
- Hosted scraping APIs can be the wrong fit for private, internal, or regulated workflows.
- Agents need operational tools, not just a function that fetches one page.

## Positioning

AgentCrawl sits between lightweight scraping libraries and hosted extraction services. It is not trying to be a prompt-only extraction demo. It is infrastructure: HTTP first, browser when needed, durable jobs, SQLite state, API, CLI, Docker, and MCP.

## What to emphasize

- Self-hosted control.
- Clean Markdown for agents.
- HTTP-first speed with optional browser fallback.
- Durable crawl jobs with checkpoints, retries, pagination, cancellation, events, and failure inspection.
- MCP support for agent clients.
- Practical operations: auth, SSRF protection, cache, usage, backup, restore, Docker.

## What not to claim

- Do not claim benchmark superiority unless backed by a reproducible benchmark.
- Do not imply it bypasses website rules or access controls.
- Do not promise it handles every difficult website.
- Do not invent adoption numbers, testimonials, or logos.

## Voice

Technical, direct, and specific. Prefer plain claims over hype. Use examples and concrete behaviors. Avoid broad AI platform language unless it names an actual interface or workflow.

## Primary CTA

Use it locally on a known URL, then connect it to an agent through MCP.
