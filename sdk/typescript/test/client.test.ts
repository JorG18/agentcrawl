import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";

import { AgentCrawlClient, AgentCrawlError } from "../src/index.ts";

type Seen = { method: string; url: string; headers: Record<string, unknown>; body: any };
const seen: Seen[] = [];
let baseUrl = "";

const server = createServer((req, res) => {
  let raw = "";
  req.on("data", (chunk) => (raw += chunk));
  req.on("end", () => {
    const body = raw ? JSON.parse(raw) : undefined;
    seen.push({ method: req.method!, url: req.url!, headers: req.headers, body });
    if (req.url === "/v1/scrape" && body.url === "https://bad.example") {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Unsupported config keys: not_a_key" }));
      return;
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ success: true, path: req.url }));
  });
});

before(async () => {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
});
after(() => server.close());

test("scrape sends the documented body and the bearer key", async () => {
  const client = new AgentCrawlClient({ baseUrl, apiKey: "secret" });

  const result = await client.scrape("https://example.com", {
    formats: ["markdown", "screenshot"],
    config: { browser_actions: [{ type: "click", selector: "#more" }] },
  });

  assert.equal(result.success, true);
  const request = seen.at(-1)!;
  assert.equal(request.url, "/v1/scrape");
  assert.equal(request.headers.authorization, "Bearer secret");
  assert.deepEqual(request.body, {
    url: "https://example.com",
    formats: ["markdown", "screenshot"],
    cache: true,
    config: { browser_actions: [{ type: "click", selector: "#more" }] },
  });
});

test("crawl sends the query only when set, plus the idempotency key", async () => {
  const client = new AgentCrawlClient({ baseUrl });

  await client.crawl("https://example.com", { maxPages: 5 });
  assert.deepEqual(seen.at(-1)!.body, {
    url: "https://example.com",
    max_pages: 5,
    wait: false,
    config: {},
  });

  await client.crawl("https://example.com", {
    query: "billing refunds",
    stopAfterIrrelevant: 2,
    idempotencyKey: "run-1",
  });
  const request = seen.at(-1)!;
  assert.equal(request.body.query, "billing refunds");
  assert.equal(request.body.stop_after_irrelevant, 2);
  assert.equal(request.headers["idempotency-key"], "run-1");
});

test("extract, extractCss, search, map and job hit their endpoints", async () => {
  const client = new AgentCrawlClient({ baseUrl });
  const schema = { type: "object", properties: { title: { type: "string" } } };

  await client.extract("https://example.com", "title", schema);
  assert.deepEqual(seen.at(-1)!.body.schema, schema);
  await client.extractCss("https://example.com", { fields: [{ name: "t", selector: "h1" }] });
  await client.search("agent crawlers", { limit: 3 });
  await client.map("https://example.com", { maxUrls: 10 });
  await client.job("job/1", { limit: 5 });

  assert.deepEqual(
    seen.slice(-5).map((request) => `${request.method} ${request.url}`),
    [
      "POST /v1/extract",
      "POST /v1/extract_css",
      "POST /v1/search",
      "POST /v1/map",
      "GET /v1/jobs/job%2F1?offset=0&limit=5",
    ],
  );
});

test("an HTTP error throws AgentCrawlError with the server's detail", async () => {
  const client = new AgentCrawlClient({ baseUrl });

  await assert.rejects(client.scrape("https://bad.example"), (error: unknown) => {
    assert.ok(error instanceof AgentCrawlError);
    assert.equal(error.status, 400);
    assert.match(error.message, /Unsupported config keys: not_a_key/);
    return true;
  });
});

test("scrapeMany asks for json with the schema, never a model", async () => {
  const client = new AgentCrawlClient({ baseUrl });
  const schema = { type: "object", properties: { name: { type: "string" } } };

  await client.scrapeMany(["https://example.com"], { formats: ["json"], json: { schema } });

  const body = seen.at(-1)!.body;
  assert.deepEqual(body.json_options, { schema });
  assert.deepEqual(body.formats, ["json"]);
});
