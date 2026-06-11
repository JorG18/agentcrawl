# Release Checklist

Use this checklist before publishing an AgentCrawl Community release to PyPI or tagging a public GitHub/GHCR release.

## 1. Local validation

```bash
python -m compileall -q agentcrawl benchmarks
python -m pytest -q
ruff check agentcrawl tests benchmarks examples
ruff format --check agentcrawl tests benchmarks examples
python benchmarks/quality_report.py
```

Required result: tests pass, lint/format pass, and quality fixtures pass above the configured threshold.

## 2. Build Python artifacts

```bash
rm -rf dist build *.egg-info
python -m build --sdist --wheel
python -m twine check dist/*
```

Expected artifacts:

```text
dist/agentcrawl_ai-<version>.tar.gz
dist/agentcrawl_ai-<version>-py3-none-any.whl
```

## 3. Clean install smoke tests

Run in fresh virtual environments, not the development checkout:

```bash
python -m venv /tmp/agentcrawl-smoke
/tmp/agentcrawl-smoke/bin/python -m pip install dist/agentcrawl_ai-<version>-py3-none-any.whl
/tmp/agentcrawl-smoke/bin/agentcrawl --version
/tmp/agentcrawl-smoke/bin/agentcrawl doctor
/tmp/agentcrawl-smoke/bin/agentcrawl scrape https://example.com
```

Also smoke optional extras when they changed:

```bash
python -m pip install 'dist/agentcrawl_ai-<version>-py3-none-any.whl[server]'
python - <<'PY'
from agentcrawl.server import app
assert app.title == 'AgentCrawl'
PY

python -m pip install 'dist/agentcrawl_ai-<version>-py3-none-any.whl[mcp]'
python - <<'PY'
import agentcrawl.mcp_server as s
assert callable(s.main)
PY

python -m pip install 'dist/agentcrawl_ai-<version>-py3-none-any.whl[docs]'
python - <<'PY'
import fitz
PY
```

## 4. Docker / GHCR

The public Community image is published at:

```text
ghcr.io/jorg18/agentcrawl:latest
ghcr.io/jorg18/agentcrawl:<version>
ghcr.io/jorg18/agentcrawl:<commit-sha>
```

Verify the published image:

```bash
docker pull ghcr.io/jorg18/agentcrawl:latest
docker run --rm ghcr.io/jorg18/agentcrawl:latest agentcrawl --version
docker run --rm ghcr.io/jorg18/agentcrawl:latest agentcrawl doctor
```

Run the API smoke test:

```bash
docker run --rm -p 8000:8000   -e AGENTCRAWL_API_KEYS="replace-with-a-long-random-key"   ghcr.io/jorg18/agentcrawl:latest

curl http://127.0.0.1:8000/health
```

The default image is intentionally lightweight and HTTP-first. Browser support is optional and should not be assumed in the default container.

## 5. Public docs sanity

Before tagging:

- README quickstart works from a clean environment.
- `INSTALL_FOR_AGENTS.md` matches the current MCP command.
- `docs/OPERATIONS.md` matches Docker/API defaults.
- `docs/EXAMPLES.md` links to copy-paste examples that exist.
- `CHANGELOG.md` lists the release version and main changes.
- No private deployment details, credentials, internal strategy, or private runbooks are committed.

## 6. Publish order

1. Push `main` and verify CI.
2. Verify GHCR image publication and smoke tests.
3. Publish PyPI only after artifact checks pass and credentials are ready.
4. Tag the release.
5. Run post-release install smoke from PyPI and GHCR.
6. Only deploy the VPS if Jorge explicitly starts a deployment phase with backup + smoke tests.
