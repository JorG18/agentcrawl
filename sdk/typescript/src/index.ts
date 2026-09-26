/**
 * Minimal TypeScript client for the AgentCrawl HTTP API (`agentcrawl-server`).
 *
 * Zero dependencies: it uses the global `fetch`, so it runs on Node 18+, Deno,
 * Bun and browsers. Every method maps to one `/v1/*` endpoint and returns the
 * server's JSON as-is; a non-2xx response throws `AgentCrawlError` carrying the
 * status and the server's error body, so failures are never silent.
 */

export type Format =
  | "markdown"
  | "html"
  | "raw_html"
  | "text"
  | "links"
  | "metadata"
  | "chunks"
  | "screenshot";

export type BrowserAction =
  | { type: "wait_for"; selector: string; timeout_ms?: number }
  | { type: "click"; selector: string }
  | { type: "type"; selector: string; text: string }
  | { type: "press"; key: string; selector?: string }
  | { type: "scroll"; times?: number }
  | { type: "wait"; ms: number };

/** Per-request engine overrides the server accepts (see OPERATIONS.md). */
export type CrawlConfig = Record<string, unknown> & {
  browser_actions?: BrowserAction[];
  ocr?: boolean;
  timeout_ms?: number;
};

export interface ScrapeOptions {
  formats?: Format[];
  onlyMainContent?: boolean;
  query?: string;
  cache?: boolean;
  cacheTtlSeconds?: number;
  config?: CrawlConfig;
}

export interface CrawlOptions {
  maxPages?: number;
  maxDepth?: number;
  include?: string[];
  exclude?: string[];
  wait?: boolean;
  query?: string;
  stopAfterIrrelevant?: number;
  idempotencyKey?: string;
  config?: CrawlConfig;
}

export type Json = Record<string, any>;

export class AgentCrawlError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "AgentCrawlError";
    this.status = status;
    this.body = body;
  }
}

export interface ClientOptions {
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
  fetch?: typeof fetch;
}

export class AgentCrawlClient {
  readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 60_000;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
  }

  scrape(url: string, options: ScrapeOptions = {}): Promise<Json> {
    return this.post("/v1/scrape", { url, ...scrapeBody(options) });
  }

  scrapeMany(urls: string[], options: ScrapeOptions = {}): Promise<Json> {
    return this.post("/v1/scrape_many", { urls, ...scrapeBody(options) });
  }

  search(
    query: string,
    options: { limit?: number; scrape?: boolean; formats?: Format[]; config?: CrawlConfig } = {},
  ): Promise<Json> {
    return this.post("/v1/search", {
      query,
      limit: options.limit ?? 5,
      scrape: options.scrape ?? true,
      formats: options.formats ?? ["markdown", "metadata"],
      config: options.config ?? {},
    });
  }

  map(url: string, options: { maxUrls?: number; config?: CrawlConfig } = {}): Promise<Json> {
    return this.post("/v1/map", { url, max_urls: options.maxUrls, config: options.config ?? {} });
  }

  crawl(url: string, options: CrawlOptions = {}): Promise<Json> {
    const body: Json = {
      url,
      max_pages: options.maxPages,
      max_depth: options.maxDepth,
      include: options.include,
      exclude: options.exclude,
      wait: options.wait ?? false,
      config: options.config ?? {},
    };
    if (options.query) {
      body.query = options.query;
      if (options.stopAfterIrrelevant !== undefined) {
        body.stop_after_irrelevant = options.stopAfterIrrelevant;
      }
    }
    const headers = options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {};
    return this.post("/v1/crawl", body, headers);
  }

  job(jobId: string, options: { offset?: number; limit?: number } = {}): Promise<Json> {
    const query = new URLSearchParams({
      offset: String(options.offset ?? 0),
      limit: String(options.limit ?? 100),
    });
    return this.request("GET", `/v1/jobs/${encodeURIComponent(jobId)}?${query}`);
  }

  /** LLM extraction; `schema` is a JSON Schema object the answer is validated against. */
  extract(url: string, prompt: string, schema?: Json, config: CrawlConfig = {}): Promise<Json> {
    return this.post("/v1/extract", { url, prompt, schema, config });
  }

  /** Deterministic CSS extraction: no LLM, same answer every run. */
  extractCss(url: string, schema: Json, config: CrawlConfig = {}): Promise<Json> {
    return this.post("/v1/extract_css", { url, schema, config });
  }

  health(): Promise<Json> {
    return this.request("GET", "/health");
  }

  private post(path: string, body: Json, headers: Record<string, string> = {}): Promise<Json> {
    return this.request("POST", path, body, headers);
  }

  private async request(
    method: string,
    path: string,
    body?: Json,
    extraHeaders: Record<string, string> = {},
  ): Promise<Json> {
    const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    const response = await this.fetchImpl(this.baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(dropUndefined(body)),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    const text = await response.text();
    let parsed: unknown = text;
    try {
      parsed = text ? JSON.parse(text) : {};
    } catch {
      // Keep the raw text; a proxy or crash page is not JSON.
    }
    if (!response.ok) {
      const detail =
        parsed && typeof parsed === "object" && "detail" in parsed
          ? JSON.stringify((parsed as Json).detail)
          : String(text).slice(0, 300);
      throw new AgentCrawlError(
        `${method} ${path} failed with HTTP ${response.status}: ${detail}`,
        response.status,
        parsed,
      );
    }
    return parsed as Json;
  }
}

function scrapeBody(options: ScrapeOptions): Json {
  return {
    formats: options.formats ?? ["markdown", "links", "metadata"],
    only_main_content: options.onlyMainContent,
    query: options.query,
    cache: options.cache ?? true,
    cache_ttl_seconds: options.cacheTtlSeconds,
    config: options.config ?? {},
  };
}

function dropUndefined(value: Json): Json {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}
