"""agentcrawl setup / doctor --client: register the MCP server in agent clients."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from agentcrawl import agent_setup
from agentcrawl.agent_setup import CLIENTS, expected_entry, state

EXPECTED = {"command": sys.executable, "args": ["-m", "agentcrawl", "mcp"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty home with no client CLI on PATH."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GEMINI_CLI_HOME", raising=False)
    monkeypatch.setattr(agent_setup.shutil, "which", lambda _name: None)
    return tmp_path


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


def test_expected_entry_pins_this_interpreter():
    assert expected_entry() == EXPECTED


@pytest.mark.parametrize(
    ("content", "expected_state"),
    [
        (None, "missing"),
        ({"mcpServers": {"agentcrawl": EXPECTED}}, "aligned"),
        ({"mcpServers": {"agentcrawl": {"command": "agentcrawl", "args": ["mcp"]}}}, "stale"),
        ("// mine\n{}", "unreadable"),
    ],
)
def test_cursor_states(home, content, expected_state):
    (home / ".cursor").mkdir()
    if content is not None:
        _write(home / ".cursor" / "mcp.json", content)
    assert state(CLIENTS["cursor"])["state"] == expected_state


def test_a_deleted_venv_is_stale(home):
    gone = {"command": str(home / "gone" / "bin" / "python"), "args": EXPECTED["args"]}
    _write(home / ".cursor" / "mcp.json", {"mcpServers": {"agentcrawl": gone}})
    result = state(CLIENTS["cursor"])
    assert result["state"] == "stale"
    assert "command not found" in result["reason"]


def test_vscode_reads_servers_key(home):
    _write(
        home / ".config" / "Code" / "User" / "mcp.json",
        {"servers": {"agentcrawl": {"type": "stdio", **EXPECTED}}},
    )
    result = state(CLIENTS["vscode"])
    assert result["detected"] is True
    assert result["state"] == "aligned"


def test_claude_code_reads_its_json(home, monkeypatch):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "cc"))
    entry = {**EXPECTED, "env": {"AGENTCRAWL_API_KEY": "s3cret"}}
    _write(home / "cc" / ".claude.json", {"mcpServers": {"agentcrawl": entry}, "other": 1})
    result = state(CLIENTS["claude_code"])
    assert result["state"] == "aligned"
    assert result["env_keys"] == ["AGENTCRAWL_API_KEY"]
    assert "s3cret" not in json.dumps(result)


def test_gemini_reads_its_settings(home, monkeypatch):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(home / "g"))
    _write(home / "g" / ".gemini" / "settings.json", {"mcpServers": {"agentcrawl": EXPECTED}})
    assert state(CLIENTS["gemini_cli"])["state"] == "aligned"


def test_codex_reads_through_its_cli(home, monkeypatch):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda name: f"/bin/{name}")
    answer = {"transport": {"type": "stdio", **EXPECTED, "env": None}}

    def run(cmd, **_kwargs):
        assert cmd == ["codex", "mcp", "get", "agentcrawl", "--json"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(answer), "WARNING: x")

    monkeypatch.setattr(agent_setup.subprocess, "run", run)
    result = state(CLIENTS["codex"])
    assert result["detected"] is True
    assert result["state"] == "aligned"

    monkeypatch.setattr(
        agent_setup.subprocess,
        "run",
        lambda cmd, **_k: subprocess.CompletedProcess(cmd, 1, "", "Error: No MCP server"),
    )
    assert state(CLIENTS["codex"])["state"] == "missing"


def test_not_detected_without_cli_or_dir(home):
    assert all(state(client)["detected"] is False for client in CLIENTS.values())
