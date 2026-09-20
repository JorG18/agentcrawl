# Agent Instructions

When asked to install, configure, connect, test, or use AgentCrawl, read and execute [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md). That file is the canonical procedure. Do not merely describe the commands when the user requested installation.

## Knowledge Graph (graphify) — query it before grepping

This repository carries a queryable knowledge graph in `graphify-out/`
(`graph.json`, `GRAPH_REPORT.md`, `graph.html`; built with graphify 0.8.35,
backend `claude-cli`). For codebase questions, prefer the graph over reading
files one by one. Run from the repo root:

```bash
graphify query "how does the crawl job scheduler persist backoff?"  # scoped subgraph for a question
graphify explain "SQLiteStore"        # one node + its neighbors, with source locations
graphify path "MCP" "SQLiteStore"     # shortest path between two concepts
graphify affected "CrawlConfig"       # reverse traversal: what a change impacts
```

Every edge is tagged `EXTRACTED` (explicit in source) or `INFERRED` (resolved
by graphify). `GRAPH_REPORT.md` has god nodes, communities, and suggested
questions for a broad architecture review.

Keep the graph current:

```bash
graphify update .   # after your own edits or `git pull`; AST-only, no LLM, fast
```

Only when docs change materially (or on a fresh clone without `graphify-out/`),
re-run the full build — code is local AST, docs use the configured LLM backend
(`claude-cli` uses the local Claude Code login; alternatively set
`GEMINI_API_KEY` / `OPENAI_API_KEY` / etc.):

```bash
graphify extract . --backend=claude-cli
```

Two gotchas that make the graph look stale (both observed 2026-09-16):

1. `update` **skips** rewriting `GRAPH_REPORT.md` / `graph.html` when the code
   topology is unchanged ("No code-graph topology changes detected"), so after an
   `extract` you must run `cluster-only` / `label` or the report goes stale against
   `graph.json`.
2. `cluster-only` re-clusters but does **not** name communities, and it rewrites the
   report header as `# Graph Report - .` without the corpus line. To name communities
   and rewrite the report in one step:

```bash
graphify label . --backend=claude-cli   # community naming + report + graph.html
```

Use the `--backend=<name>` (equals) form: `cluster-only` / `label` silently ignore the
space-separated `--backend <name>` form and leave `Community N` placeholders.
