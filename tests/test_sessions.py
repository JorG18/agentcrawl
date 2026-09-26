"""Saved browser sessions: names only, owner-only files, honest errors."""

from __future__ import annotations

import json

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.config import CrawlConfig
from agentcrawl.sessions import (
    delete_session,
    list_sessions,
    require_session,
    save_storage_state,
    session_path,
    validate_session_name,
)


@pytest.mark.parametrize("name", ["../etc/passwd", "a/b", "", ".hidden", "x" * 65, "a..b", 3])
def test_session_names_cannot_become_paths(name) -> None:
    with pytest.raises(ValueError):
        validate_session_name(name)
    with pytest.raises(ValueError):
        CrawlConfig.from_dict({"browser_session": name})


def test_session_files_live_under_the_sessions_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_SESSIONS_DIR", str(tmp_path))
    assert session_path("work-wiki") == tmp_path / "work-wiki.json"


def test_missing_session_is_a_config_error_that_says_how_to_fix_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_SESSIONS_DIR", str(tmp_path))
    with pytest.raises(Exception) as info:
        require_session("nope")
    assert "agentcrawl login" in str(info.value)
    assert getattr(info.value, "error_type", None) == "config_error"


def test_saved_state_is_owner_only_and_listed_without_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_SESSIONS_DIR", str(tmp_path / "sessions"))
    state = {"cookies": [{"name": "sid", "value": "secret", "domain": ".example.org"}]}

    class Context:
        def storage_state(self):
            return state

    path = save_storage_state(Context(), "work")
    assert json.loads(path.read_text("utf-8")) == state
    assert path.stat().st_mode & 0o077 == 0
    listed = list_sessions()
    assert listed[0]["name"] == "work"
    assert listed[0]["domains"] == [".example.org"]
    assert "secret" not in json.dumps(listed)
    assert delete_session("work") is True
    assert delete_session("work") is False


def test_session_scrape_without_a_saved_session_fails_honestly(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr("agentcrawl.fetchers.validate_remote_url", lambda *a, **k: None)
    doc = AgentCrawl({"browser_session": "missing", "respect_robots_txt": False}).scrape(
        "https://wiki.example.org/private"
    )
    assert not doc.ok
    assert "agentcrawl login" in doc.errors[0]


def test_api_requests_cannot_pick_a_session() -> None:
    from agentcrawl.server import _ALLOWED_CONFIG_OVERRIDES

    assert "browser_session" not in _ALLOWED_CONFIG_OVERRIDES
