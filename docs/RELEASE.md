# Release Checklist

Use this checklist before publishing an AgentCrawl Community release to PyPI or GHCR.

## 1. Local validation

```bash
python -m compileall -q agentcrawl
python -m pytest -q
ruff check agentcrawl tests examples
```

Required result: tests pass, lint passes, and no new warnings beyond known third-party deprecations.

## 2. Build Python artifacts

```bash
rm -rf dist
python -m build --sdist --wheel
python -m twine check dist/*
```

Expected artifacts:

```text
dist/agentcrawl-<version>.tar.gz
dist/agentcrawl-<version>-py3-none-any.whl
```

## 3. Clean install smoke tests

Run in fresh virtual environments, not the development checkout:

```bash
python -m venv /tmp/agentcrawl-base
/tmp/agentcrawl-base/bin/python -m pip install dist/agentcrawl-<version>-py3-none-any.whl
/tmp/agentcrawl-base/bin/agentcrawl --version
/tmp/agentcrawl-base/bin/agentcrawl doctor
```

Also smoke optional extras:

```bash
python -m pip install 'dist/agentcrawl-<version>-py3-none-any.whl[server]'
python - <<'PY'
from agentcrawl.server import app
assert app.title == 'AgentCrawl'
PY

python -m pip install 'dist/agentcrawl-<version>-py3-none-any.whl[mcp]'
python - <<'PY'
import agentcrawl.mcp_server as s
assert callable(s.main)
PY

python -m pip install 'dist/agentcrawl-<version>-py3-none-any.whl[docs]'
python - <<'PY'
import fitz
PY
```

## 4. Docker / GHCR

A Docker-capable machine or GitHub Actions is required for the image build smoke test:

```bash
docker build -t agentcrawl-community:release .
docker run --rm -p 8000:8000 \
  -e AGENTCRAWL_API_KEYS=release-smoke-key \
  agentcrawl-community:release
curl http://127.0.0.1:8000/health
```

Expected public image path after publication:

```text
ghcr.io/jorg18/agentcrawl:<version>
ghcr.io/jorg18/agentcrawl:latest
```

## 5. Public docs sanity

Before tagging:

- README quickstart works from a clean environment.
- `INSTALL_FOR_AGENTS.md` matches the current MCP command.
- `docs/OPERATIONS.md` still matches Docker/API defaults.
- `CHANGELOG.md` lists the release version and main changes.
- No private deployment details, credentials, internal strategy, or private runbooks are committed.

## 6. Publish order

1. Push `main` and verify CI.
2. Tag the release.
3. Verify GitHub Actions builds/publishes GHCR image.
4. Publish PyPI package only after local artifact checks pass.
5. Run post-release install smoke from PyPI and GHCR.
6. Only then deploy the VPS if Jorge wants runtime updated.
