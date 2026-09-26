# AgentCrawl TypeScript client

A minimal, zero-dependency client for the AgentCrawl HTTP API
(`agentcrawl-server` or the Docker image). It uses the global `fetch`, so it
runs on Node, Deno, Bun and in browsers.

```ts
import { AgentCrawlClient } from "./src/index.ts";

const client = new AgentCrawlClient({
  baseUrl: "http://127.0.0.1:8000",
  apiKey: process.env.AGENTCRAWL_API_KEY,
});

const page = await client.scrape("https://example.com", { formats: ["markdown", "chunks"] });
const run = await client.crawl("https://docs.example.com", {
  query: "billing refunds",
  maxPages: 20,
  wait: true,
});
const data = await client.extract("https://example.com/pricing", "List the plans", {
  type: "object",
  properties: { plans: { type: "array", items: { type: "string" } } },
  required: ["plans"],
});
```

Methods: `scrape`, `scrapeMany`, `search`, `map`, `crawl`, `job`, `extract`,
`extractCss`, `health`. A non-2xx response throws `AgentCrawlError` with the
HTTP `status` and the server's error `body`.

The package is not on npm yet; copy `src/index.ts` into your project or
install it from the repository. Tests: `npm test` (Node 22.18+ runs the
TypeScript directly).
