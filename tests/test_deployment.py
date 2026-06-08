from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_has_safe_runtime_defaults() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM mcr.microsoft.com/playwright/python:" in dockerfile
    assert "AGENTCRAWL_DB=/data/agentcrawl.db" in dockerfile
    assert "AGENTCRAWL_AUTH_ENABLED=true" in dockerfile
    assert "AGENTCRAWL_ALLOW_LOCAL_FILES=false" in dockerfile
    assert "AGENTCRAWL_ALLOW_PRIVATE_NETWORK=false" in dockerfile
    assert 'pip install --no-cache-dir -e ".[server,mcp]"' in dockerfile
    assert "USER agentcrawl" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_docker_compose_uses_persistent_data_and_hardening() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "env_file:" in compose
    assert "- .env" in compose
    assert "agentcrawl-data:/data" in compose
    assert "tmpfs:" in compose
    assert "shm_size: 512mb" in compose
    assert "no-new-privileges:true" in compose
    assert "restart: unless-stopped" in compose
    assert "agentcrawl-data:" in compose


def test_env_example_contains_required_server_and_client_settings() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = {
        "AGENTCRAWL_AUTH_ENABLED",
        "AGENTCRAWL_API_KEYS",
        "AGENTCRAWL_OWNER_API_KEYS",
        "AGENTCRAWL_DB",
        "AGENTCRAWL_FETCHER",
        "AGENTCRAWL_BROWSER_FALLBACK",
        "AGENTCRAWL_DOMAIN_MAX_CONCURRENCY",
        "AGENTCRAWL_WORKERS",
        "AGENTCRAWL_CRAWL_JOB_PAGE_QUANTUM",
        "AGENTCRAWL_BASE_URL",
        "AGENTCRAWL_API_KEY",
    }
    configured = {
        line.split("=", 1)[0]
        for line in env_example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert required <= configured
    assert "change-me-dev-key" in env_example
