"""Register AgentCrawl's MCP server in agent clients (``agentcrawl setup``).

Clients that ship an ``mcp add`` command (Claude Code, Codex, Gemini CLI) are
changed only through it: Claude Code rewrites ``~/.claude.json`` while it runs,
so editing that file could be lost or clobber its state. Clients without one
(Cursor, VS Code) get a backed-up, atomic JSON write.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

SERVER = "agentcrawl"


def expected_entry() -> dict[str, Any]:
    # This interpreter, not the ``agentcrawl`` script: works in a venv or pipx
    # without the client inheriting PATH; doctor catches a deleted venv.
    return {"command": sys.executable, "args": ["-m", "agentcrawl", "mcp"]}


def _claude_json() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def _gemini_settings() -> Path:
    return Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini" / "settings.json"


def _cursor_file() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _vscode_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    if sys.platform == "win32":
        return (
            Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Code" / "User"
        )
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Code" / "User"


class Unreadable(Exception):
    """The client's configuration exists but cannot be read safely."""


def _json_entry(path: Path, key: str) -> dict | None:
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path} is not plain JSON ({exc})") from exc
    servers = document.get(key) if isinstance(document, dict) else None
    entry = servers.get(SERVER) if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else None


def _codex_entry() -> dict | None:
    result = subprocess.run(
        ["codex", "mcp", "get", SERVER, "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        transport = json.loads(result.stdout).get("transport") or {}
    except (ValueError, AttributeError) as exc:
        raise Unreadable(f"codex mcp get returned no JSON ({exc})") from exc
    return {key: transport.get(key) for key in ("command", "args", "env")}


@dataclass(frozen=True)
class Client:
    name: str
    display: str
    detect: Callable[[], bool]
    read: Callable[[], dict | None]
    kind: Literal["cli", "file"]


def _on_path(command: str) -> Callable[[], bool]:
    return lambda: shutil.which(command) is not None


CLIENTS: dict[str, Client] = {
    "claude_code": Client(
        "claude_code",
        "Claude Code",
        _on_path("claude"),
        lambda: _json_entry(_claude_json(), "mcpServers"),
        "cli",
    ),
    "codex": Client("codex", "Codex", _on_path("codex"), _codex_entry, "cli"),
    "gemini_cli": Client(
        "gemini_cli",
        "Gemini CLI",
        _on_path("gemini"),
        lambda: _json_entry(_gemini_settings(), "mcpServers"),
        "cli",
    ),
    "cursor": Client(
        "cursor",
        "Cursor",
        lambda: _cursor_file().parent.is_dir(),
        lambda: _json_entry(_cursor_file(), "mcpServers"),
        "file",
    ),
    "vscode": Client(
        "vscode",
        "VS Code",
        lambda: _vscode_dir().is_dir(),
        lambda: _json_entry(_vscode_dir() / "mcp.json", "servers"),
        "file",
    ),
}


def _command_exists(command: str) -> bool:
    return os.path.exists(command) if os.path.isabs(command) else shutil.which(command) is not None


def state(client: Client) -> dict[str, Any]:
    """Whether ``client`` has an ``agentcrawl`` registration this install would write.

    Env values are never returned, only their keys.
    """
    report: dict[str, Any] = {
        "client": client.name,
        "detected": client.detect(),
        "state": "missing",
        "reason": None,
        "env_keys": [],
    }
    if not report["detected"] and client.kind == "cli":
        # Without its CLI the client's registration can neither be read nor changed.
        return report
    try:
        entry = client.read()
    except (Unreadable, OSError, subprocess.SubprocessError) as exc:
        report.update(state="unreadable", reason=str(exc)[:300])
        return report
    if entry is None:
        return report
    report["env_keys"] = sorted(entry.get("env") or {})
    expected = expected_entry()
    command, args = entry.get("command"), list(entry.get("args") or [])
    if not command or not _command_exists(str(command)):
        report.update(state="stale", reason=f"command not found: {command}")
    elif command != expected["command"] or args != expected["args"]:
        report.update(state="stale", reason=f"registered as: {command} {' '.join(map(str, args))}")
    else:
        report["state"] = "aligned"
    return report
