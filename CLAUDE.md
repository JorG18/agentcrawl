# Claude Code Instructions

For AgentCrawl installation, MCP registration, verification, and normal tool-selection rules, read and execute [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md). Preserve unrelated client configuration and never expose credentials.

## Knowledge Graph (graphify)

A queryable knowledge graph lives in `graphify-out/`. For codebase questions,
query it before reading files one by one (run from the repo root):

```bash
graphify query "<question>"
graphify explain "<Symbol>"
graphify path "<A>" "<B>"
graphify affected "<Symbol>"
```

After code edits or `git pull`, refresh with `graphify update .` (AST-only, no
LLM). Full rebuild (needed only for doc changes or fresh clones):
`graphify extract . --backend=claude-cli`, then
`graphify label . --backend=claude-cli` to re-cluster, name communities and
rewrite `GRAPH_REPORT.md` / `graph.html`. Note: `update` leaves those two outputs
untouched when the code topology is unchanged, and `cluster-only`/`label` only
honor the `--backend=<name>` (equals) form. See `graphify-out/GRAPH_REPORT.md`
and the Knowledge Graph section in [AGENTS.md](AGENTS.md).
