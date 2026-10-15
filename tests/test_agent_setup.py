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


# -- Task 2: apply -------------------------------------------------------------
from agentcrawl.agent_setup import apply, plan  # noqa: E402


def _stale(env=None):
    entry = {"command": "agentcrawl", "args": ["mcp"]}
    return {**entry, "env": env} if env else entry


def test_apply_cursor_adds_and_keeps_other_servers(home):
    path = home / ".cursor" / "mcp.json"
    _write(path, {"mcpServers": {"other": {"command": "x"}}, "theme": 1})

    result = apply(CLIENTS["cursor"])

    assert result["ok"] is True
    document = json.loads(path.read_text())
    assert document["mcpServers"]["other"] == {"command": "x"}
    assert document["theme"] == 1
    assert document["mcpServers"]["agentcrawl"] == EXPECTED
    [backup] = list(path.parent.glob("mcp.json.bak-*"))
    assert json.loads(backup.read_text())["mcpServers"] == {"other": {"command": "x"}}
    assert state(CLIENTS["cursor"])["state"] == "aligned"


def test_apply_vscode_writes_type_stdio(home):
    (home / ".config" / "Code" / "User").mkdir(parents=True)
    assert apply(CLIENTS["vscode"])["ok"] is True
    path = home / ".config" / "Code" / "User" / "mcp.json"
    assert json.loads(path.read_text()) == {
        "servers": {"agentcrawl": {"type": "stdio", **EXPECTED}}
    }


def test_jsonc_file_is_refused_untouched(home):
    path = home / ".cursor" / "mcp.json"
    _write(path, '// mine\n{"mcpServers": {}}')
    before = path.read_bytes()

    result = apply(CLIENTS["cursor"])

    assert result["ok"] is False
    assert str(path) in result["detail"] and "agentcrawl" in result["detail"]
    assert path.read_bytes() == before
    assert not list(path.parent.glob("mcp.json.bak-*"))


def test_update_keeps_existing_env(home):
    path = home / ".cursor" / "mcp.json"
    _write(path, {"mcpServers": {"agentcrawl": _stale({"AGENTCRAWL_API_KEY": "k"})}})
    assert apply(CLIENTS["cursor"])["ok"] is True
    entry = json.loads(path.read_text())["mcpServers"]["agentcrawl"]
    assert entry == {**EXPECTED, "env": {"AGENTCRAWL_API_KEY": "k"}}


@pytest.mark.parametrize(
    ("name", "remove", "add"),
    [
        (
            "claude_code",
            ["claude", "mcp", "remove", "-s", "user", "agentcrawl"],
            [
                "claude",
                "mcp",
                "add",
                "-s",
                "user",
                "agentcrawl",
                "-e",
                "AGENTCRAWL_API_KEY=k",
                "--",
                sys.executable,
                "-m",
                "agentcrawl",
                "mcp",
            ],
        ),
        (
            "gemini_cli",
            ["gemini", "mcp", "remove", "-s", "user", "agentcrawl"],
            [
                "gemini",
                "mcp",
                "add",
                "-s",
                "user",
                "-e",
                "AGENTCRAWL_API_KEY=k",
                "agentcrawl",
                sys.executable,
                "--",
                "-m",
                "agentcrawl",
                "mcp",
            ],
        ),
        (
            "codex",
            ["codex", "mcp", "remove", "agentcrawl"],
            [
                "codex",
                "mcp",
                "add",
                "agentcrawl",
                "--env",
                "AGENTCRAWL_API_KEY=k",
                "--",
                sys.executable,
                "-m",
                "agentcrawl",
                "mcp",
            ],
        ),
    ],
)
def test_cli_update_removes_then_adds_with_env(home, monkeypatch, name, remove, add):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda cmd: f"/bin/{cmd}")
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(agent_setup.subprocess, "run", run)
    monkeypatch.setattr(agent_setup, "_read", lambda client: _stale({"AGENTCRAWL_API_KEY": "k"}))

    assert apply(CLIENTS[name])["ok"] is True
    assert calls == [remove, add]


def test_aligned_client_is_left_alone(home):
    path = home / ".cursor" / "mcp.json"
    _write(path, {"mcpServers": {"agentcrawl": EXPECTED}})
    mtime = path.stat().st_mtime_ns
    assert plan(CLIENTS["cursor"])["action"] == "none"
    assert apply(CLIENTS["cursor"])["ok"] is True
    assert path.stat().st_mtime_ns == mtime


def test_plan_never_shows_env_values(home, monkeypatch):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda cmd: f"/bin/{cmd}")
    monkeypatch.setattr(agent_setup, "_read", lambda client: _stale({"AGENTCRAWL_API_KEY": "k"}))
    steps = "\n".join(plan(CLIENTS["claude_code"])["steps"])
    assert "AGENTCRAWL_API_KEY" in steps
    assert "=k" not in steps


def test_a_json_file_that_is_not_an_object_is_refused(home):
    path = home / ".cursor" / "mcp.json"
    _write(path, "[]")
    result = apply(CLIENTS["cursor"])
    assert result["ok"] is False
    assert path.read_text() == "[]"


# -- Task 3: CLI -----------------------------------------------------------------
from agentcrawl.cli import main  # noqa: E402


def _tty(monkeypatch, answers=None):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: answers is not None)
    replies = iter(answers or [])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(replies))


def test_setup_without_tty_needs_yes(home, monkeypatch, capsys):
    (home / ".cursor").mkdir()
    _tty(monkeypatch)
    assert main(["setup"]) == 2
    assert not (home / ".cursor" / "mcp.json").exists()
    assert "mcp.json" in capsys.readouterr().out


def test_setup_yes_applies_selected_client(home, monkeypatch):
    (home / ".cursor").mkdir()
    _tty(monkeypatch)
    assert main(["setup", "--client", "cursor", "--yes"]) == 0
    assert state(CLIENTS["cursor"])["state"] == "aligned"


def test_setup_dry_run_writes_nothing(home, monkeypatch):
    (home / ".cursor").mkdir()
    _tty(monkeypatch)
    assert main(["setup", "--client", "cursor", "--dry-run"]) == 0
    assert not (home / ".cursor" / "mcp.json").exists()


def test_setup_tty_defaults(home, monkeypatch):
    (home / ".cursor").mkdir()
    vscode = home / ".config" / "Code" / "User" / "mcp.json"
    _write(vscode, {"servers": {"agentcrawl": _stale()}})
    _tty(monkeypatch, ["", "", "y"])  # Cursor: default No; VS Code: default Yes; apply

    assert main(["setup"]) == 0

    assert not (home / ".cursor" / "mcp.json").exists()
    assert state(CLIENTS["vscode"])["state"] == "aligned"


def test_one_failing_cli_does_not_stop_the_rest(home, monkeypatch, capsys):
    (home / ".cursor").mkdir()
    monkeypatch.setattr(
        agent_setup.shutil, "which", lambda cmd: "/bin/claude" if cmd == "claude" else None
    )
    monkeypatch.setattr(
        agent_setup.subprocess,
        "run",
        lambda cmd, **_k: subprocess.CompletedProcess(cmd, 1, "", "Not logged in\nmore"),
    )
    _tty(monkeypatch)

    code = main(["setup", "--client", "claude_code", "--client", "cursor", "--yes"])

    out = capsys.readouterr().out
    assert code == 1
    assert "Claude Code: add -> failed: Not logged in" in out
    assert "Cursor: add -> ok" in out
    assert state(CLIENTS["cursor"])["state"] == "aligned"


def test_doctor_client_all_reports_fix(home, monkeypatch, capsys):
    _write(home / ".cursor" / "mcp.json", {"mcpServers": {"agentcrawl": _stale()}})
    assert main(["doctor", "--client", "all"]) == 0
    report = json.loads(capsys.readouterr().out)
    [cursor] = [entry for entry in report["clients"] if entry["client"] == "cursor"]
    assert cursor["state"] == "stale"
    assert cursor["fix"] == "agentcrawl setup --client cursor"


def test_setup_dry_run_hides_env_values(home, monkeypatch, capsys):
    monkeypatch.setattr(agent_setup.shutil, "which", lambda cmd: f"/bin/{cmd}")
    monkeypatch.setattr(agent_setup, "_read", lambda client: _stale({"AGENTCRAWL_API_KEY": "k"}))
    _tty(monkeypatch)
    assert main(["setup", "--client", "claude_code", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "AGENTCRAWL_API_KEY" in out
    assert "=k" not in out
