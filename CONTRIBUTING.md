# Contributing

AgentCrawl is small on purpose. Help us keep it that way. 🕷️

## Ground rules

1. **Keep commits atomic.** One logical change per commit. Easier to review, easier to revert, easier to bisect when something breaks at 3 a.m.
2. **Stay in scope.** A bug fix is not the moment to refactor the surrounding module. Drive-by cleanups belong in a separate commit.
3. **Add tests with behavior changes.** If you fixed something, prove it stays fixed. If you added a flag, prove the flag actually does what its docstring claims.
4. **Run the gate before pushing:**
   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
   .venv/bin/ruff check agentcrawl tests examples
   .venv/bin/ruff format --check agentcrawl tests examples
   ```
5. **Do not commit credentials, deployment addresses, databases, scraped content, or private Enhanced modules.** The Community surface stays public; Enhanced lives in a private mirror that has no remote.
6. **In the PR, write the *why*, not just the *what*.** What changed, what you tested, anything you couldn't test locally (e.g. browser-specific behaviour), and which surfaces you touched. Three sentences is fine. A wall of pasted diff is not.

## Picking up an issue

- **Bug reports** — open an issue with a minimal repro (URL or local HTML, the exact CLI line or Python call, what happened vs what you expected). If it surfaces via a log line, paste the line, not the whole file.
- **Feature proposals** — open an issue first. AgentCrawl has a hard community boundary (see `README.md` → "Edge case: `example.com`"), and not every good idea belongs in the open repo. Discussion before code is not bureaucracy; it is how we keep the product coherent.
- **Documentation fixes** — straight PRs are welcome. Spelling, broken links, unclear sentences. Small docs PRs ship fast.

## First time here?

The shortest path to a merged PR:

1. Pick an issue labelled `good first issue` (they're scoped and small).
2. Fork, branch, follow the ground rules above.
3. Open the PR against `main`. The CI badge in `README.md` shows the lint + test gate.

Thanks for helping keep AgentCrawl small, honest, and useful. 🙏

---

By contributing, you agree that your contribution is licensed under Apache License 2.0.
