# Visual Assets

AgentCrawl is a developer product, so the visuals should make the value obvious in seconds: agents need web context, AgentCrawl turns the web into clean, self-hosted context.

Use this checklist when creating images for the README, GitHub social preview, docs, launch posts, and website.

## Priority Assets 🎨

| Asset | Size | Use | Direction |
| --- | --- | --- | --- |
| GitHub social preview / Open Graph | 1280x640 | Repository preview, social shares | Product name, short tagline, clean web-to-agent extraction visual. |
| README hero image | 1600x900 or 1400x700 | Top of README | Show messy web pages becoming clean Markdown for agents. |
| Architecture diagram | 1600x900 | README and docs | URL input -> HTTP fetch -> browser fallback -> parser -> cache/jobs -> Markdown/API/MCP. |
| MCP workflow diagram | 1600x900 | Agent docs | Agent client -> MCP -> AgentCrawl -> web -> clean context returned. |
| Crawl jobs diagram | 1600x900 | Jobs docs | Queue, checkpoints, retries, pagination, failures, cache. |
| Terminal screenshot | 1600x1000 | README quickstart | `agentcrawl scrape https://example.com` returning clean Markdown. |
| API screenshot | 1600x1000 | HTTP API section | `/docs` or a successful `/v1/scrape` response. |
| Short demo GIF | 1200x800 | README and launch posts | Install, scrape, output Markdown, then run MCP or API. |
| Logo mark | 512x512 SVG/PNG | GitHub avatar, favicon | Simple crawler/web/agent mark, readable at small size. |

## Suggested Visual Style ✨

- Clean, technical, modern, not corporate stock art.
- Dark text on light backgrounds or high-contrast dark mode.
- Use 2-3 accent colors maximum.
- Show real product concepts: URLs, Markdown, jobs, cache, MCP, API.
- Avoid vague AI clouds, abstract brains, generic robot faces, and noisy dashboards.
- Keep text short enough to read in a GitHub preview card.

## Copy For Images

Use one of these lines depending on the asset:

```text
Self-hosted web extraction for AI agents
```

```text
Turn messy web pages into clean Markdown, API responses, and MCP tools
```

```text
Your crawler. Your cache. Your jobs. Your data.
```

```text
HTTP-first scraping with durable crawl jobs and MCP access
```

## GPT Image Prompts

### GitHub Social Preview

```text
Create a clean GitHub social preview image for a developer tool named AgentCrawl. It is a self-hosted web extraction product for AI agents. Visual metaphor: messy web pages and HTML on the left transforming into clean Markdown/API/MCP context on the right. Include the text: "AgentCrawl" and "Self-hosted web extraction for AI agents". Modern technical style, crisp UI panels, subtle depth, high contrast, no cartoon robots, no clutter, 1280x640.
```

### README Hero

```text
Create a polished README hero image for AgentCrawl, a self-hosted web extraction tool for AI agents. Show a pipeline from URL input to clean Markdown, cache, durable crawl jobs, and MCP tools. The image should feel like a serious open-source developer product: clean UI, terminal snippets, web page cards, Markdown output, small icons for API/Docker/MCP. Include the text "AgentCrawl" and "Clean web context for AI agents". 1600x900.
```

### Architecture Diagram

```text
Create a clear technical architecture diagram for AgentCrawl. Flow: URL or site input -> HTTP fetch -> optional browser fallback -> parsing and cleanup -> SQLite cache/jobs/retries -> outputs: Markdown, text, links, metadata, structured data, HTTP API, CLI, MCP. Use simple boxes and arrows, readable labels, modern developer documentation style, light background, 1600x900.
```

### MCP Workflow Diagram

```text
Create a developer documentation diagram showing an AI agent using MCP to call AgentCrawl. Flow: Agent client -> MCP server -> AgentCrawl -> target website -> clean Markdown/context back to the agent. Include small labels for scrape, map, crawl, jobs, usage, cache. Clean technical style, no mascots, 1600x900.
```

## README Placement

Recommended order:

1. Add the GitHub social preview in repository settings.
2. Add the README hero image under the badges and before the tagline.
3. Add the architecture diagram near the HTTP/API/MCP sections.
4. Add a short demo GIF after Quick start.

Keep the README fast to scan. One strong hero plus one architecture diagram is better than many decorative images.
