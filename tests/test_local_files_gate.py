"""Local-file access gate (S1 / stress-test finding C1).

The MCP local engine is driven by an agent, and an agent's input can come from
a hostile page. It used to hand every non-URL source straight to the local
file reader, so ``scrape_url("~/.pypirc")`` returned the file. The gate now
lives in ``fetchers.fetch_source`` so every entrance (scrape, batch, extract,
map, crawl) is covered by the same check.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.cli import _local_config
from agentcrawl.config import CrawlConfig, config_from_env
from agentcrawl.mcp_server import (
    crawl_site,
    extract_structured,
    map_site,
    scrape_many,
    scrape_url,
)

SECRET = "TOP-SECRET-TOKEN-4242"


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret.html"
    path.write_text(f"<main><h1>{SECRET}</h1></main>", encoding="utf-8")
    return path


@pytest.fixture
def mcp_local(monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTCRAWL_ALLOW_LOCAL_FILES", raising=False)
    monkeypatch.delenv("AGENTCRAWL_LOCAL_FILES_ROOT", raising=False)


def _leaks(result: object) -> bool:
    return SECRET in json.dumps(result, default=str)


def test_every_mcp_tool_refuses_local_files_by_default(mcp_local, secret_file: Path) -> None:
    source = str(secret_file)

    scraped = scrape_url(source)
    assert not _leaks(scraped)
    assert scraped["metadata"]["error_type"] == "local_files_disabled"

    batch = scrape_many([source])
    assert not _leaks(batch)
    assert "local_files_disabled" in json.dumps(batch)

    extracted = extract_structured(source, {"title": "h1"})
    assert not _leaks(extracted)
    assert extracted["success"] is False

    assert not _leaks(map_site(source))

    crawled = crawl_site(source)
    assert not _leaks(crawled)
    assert "local_files_disabled" in json.dumps(crawled)


def test_mcp_refuses_tilde_and_relative_paths(mcp_local, tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "rel.html").write_text(f"<p>{SECRET}</p>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not _leaks(scrape_url("rel.html"))
    assert not _leaks(scrape_url("~/rel.html"))


def test_mcp_opt_in_with_root_confines_reads(
    mcp_local, tmp_path: Path, secret_file: Path, monkeypatch
) -> None:
    jail = tmp_path / "jail"
    jail.mkdir()
    (jail / "page.html").write_text("<main><h1>Jailed page</h1></main>", encoding="utf-8")
    (jail / "link.html").symlink_to(secret_file)
    monkeypatch.setenv("AGENTCRAWL_ALLOW_LOCAL_FILES", "true")
    monkeypatch.setenv("AGENTCRAWL_LOCAL_FILES_ROOT", str(jail))

    assert "Jailed page" in scrape_url(str(jail / "page.html"))["markdown"]

    for escape in (
        str(secret_file),
        str(jail / ".." / "secret.html"),
        str(jail / "link.html"),  # symlink pointing out of the jail
    ):
        result = scrape_url(escape)
        assert not _leaks(result), escape
        assert result["metadata"]["error_type"] == "local_file_outside_root", escape


def test_mcp_opt_in_without_root_reads_anywhere(mcp_local, secret_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_ALLOW_LOCAL_FILES", "true")
    assert SECRET in scrape_url(str(secret_file))["markdown"]


def test_root_prefix_sibling_is_not_inside(tmp_path: Path) -> None:
    """``/data/jail-evil`` must not pass a ``/data/jail`` prefix check."""
    jail = tmp_path / "jail"
    jail.mkdir()
    sibling = tmp_path / "jail-evil"
    sibling.mkdir()
    (sibling / "x.html").write_text(f"<p>{SECRET}</p>", encoding="utf-8")
    crawler = AgentCrawl({"allow_local_files": True, "local_files_root": str(jail)})
    document = crawler.scrape(str(sibling / "x.html"))
    assert SECRET not in document.markdown
    assert document.metadata["error_type"] == "local_file_outside_root"


def test_library_default_still_reads_local_docs(secret_file: Path) -> None:
    assert CrawlConfig().allow_local_files is True
    assert SECRET in AgentCrawl().scrape(str(secret_file)).markdown


def test_library_can_disable_local_files(secret_file: Path) -> None:
    document = AgentCrawl({"allow_local_files": False}).scrape(str(secret_file))
    assert SECRET not in document.markdown
    assert document.metadata["error_type"] == "local_files_disabled"


def test_cli_local_mode_keeps_local_files_on(mcp_local) -> None:
    import argparse

    config = _local_config(argparse.Namespace(fetcher="http"))
    assert config["allow_local_files"] is True


def test_env_can_turn_local_files_off_for_the_cli(monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_ALLOW_LOCAL_FILES", "false")
    assert config_from_env(allow_local_files_default=True)["allow_local_files"] is False


def test_config_from_env_defaults_to_refusing_local_files(mcp_local) -> None:
    config = config_from_env()
    assert config["allow_local_files"] is False
    assert "local_files_root" not in config or config["local_files_root"] is None


def test_root_is_realpathed(tmp_path: Path, monkeypatch) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    (real / "p.html").write_text("<p>inside</p>", encoding="utf-8")
    monkeypatch.setenv("AGENTCRAWL_LOCAL_FILES_ROOT", str(alias))
    config = config_from_env()
    assert config["local_files_root"] == os.path.realpath(real)
    crawler = AgentCrawl({**config, "allow_local_files": True})
    assert "inside" in crawler.scrape(str(alias / "p.html")).markdown
