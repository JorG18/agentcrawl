# Release Checklist

Use this checklist before publishing an AgentCrawl Community release to PyPI or tagging a public GitHub/GHCR release.

## 1. Local validation

```bash
python -m compileall -q agentcrawl benchmarks
python -m pytest -q
ruff check agentcrawl tests benchmarks examples
ruff format --check agentcrawl tests benchmarks examples
python -m benchmarks.quality_report
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
/tmp/agentcrawl-smoke/bin/agentcrawl scrape https://pypi.org/project/agentcrawl-ai/
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

Releases are cut by `.github/workflows/release.yml`:

1. Bump `version` in `pyproject.toml` and turn `## Unreleased` in `CHANGELOG.md` into `## X.Y.Z - YYYY-MM-DD`; merge to `main` with CI green.
2. Push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`. The workflow checks the tag matches the package version, runs lint and tests, builds and `twine check`s the artifacts, publishes to PyPI and creates the GitHub Release with that version's CHANGELOG section as notes. `docker.yml` publishes the GHCR image for the tag.
3. Without push access for tags, run **Release** by hand on `main` with the tag name: it creates the tag at that commit. Then run **Build and Push Docker Image** by hand on the new tag, because a tag created by a workflow does not start other workflows.
4. PyPI uses trusted publishing: the `agentcrawl-ai` project on pypi.org must list this repository, workflow `release.yml` and environment `pypi` as a trusted publisher. Until then the PyPI job fails and the GitHub Release is still created.
5. Run the post-release install smoke from PyPI and GHCR (sections 3 and 4).
6. Deploy hosted instances only as a separate, explicit step with a fresh backup and smoke tests.
