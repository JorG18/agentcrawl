"""Named browser sessions: log in once by hand, then scrape as that user.

Pages behind a login (an internal wiki, a dashboard, a paid docs site) used to
be out of reach: every fetch started a fresh browser with no cookies. A
session is a Playwright ``storage_state`` file (cookies plus local storage)
saved under :func:`sessions_dir` by ``agentcrawl login``. A scrape with
``browser_session="<name>"`` loads it into the browser and writes the
refreshed cookies back afterwards, so a rolling session stays alive.

Sessions are addressed by *name*, never by path: the name is checked against
:data:`_NAME_RE`, so no caller (MCP tool, config file, API default) can make
AgentCrawl read or write an arbitrary file. The files hold live credentials;
they are written with owner-only permissions and are never logged or put in
document metadata. The HTTP API does not accept a session per request; an
operator can set one for the whole server with ``AGENTCRAWL_BROWSER_SESSION``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from .exceptions import FetchError

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def sessions_dir() -> Path:
    configured = os.getenv("AGENTCRAWL_SESSIONS_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".agentcrawl" / "sessions"
    return base


def validate_session_name(name: Any) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name) or ".." in name:
        raise ValueError(
            "browser_session must be a name of letters, digits, '.', '_' or '-' "
            f"(at most 64 characters), got {name!r}"
        )
    return name


def session_path(name: str) -> Path:
    return sessions_dir() / f"{validate_session_name(name)}.json"


def require_session(name: str) -> Path:
    """Path of an existing session, or a ``config_error`` that says how to create it."""
    path = session_path(name)
    if not path.is_file():
        raise FetchError(
            f"No saved browser session {name!r}. Create it with: "
            f"agentcrawl login <url> --session {name}",
            error_type="config_error",
        )
    return path


def save_storage_state(context: Any, name: str) -> Path:
    """Write ``context``'s cookies and storage to the session file (mode 0600)."""
    path = session_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    state = context.storage_state()
    fd, temp = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return path


def list_sessions() -> list[dict[str, Any]]:
    """Saved sessions with the cookie domains they cover (no cookie values)."""
    directory = sessions_dir()
    if not directory.is_dir():
        return []
    sessions = []
    for path in sorted(directory.glob("*.json")):
        try:
            state = json.loads(path.read_text("utf-8"))
            domains = sorted({cookie.get("domain", "") for cookie in state.get("cookies", [])})
        except (OSError, ValueError, AttributeError):
            domains = []
        sessions.append({"name": path.stem, "domains": domains, "path": str(path)})
    return sessions


def delete_session(name: str) -> bool:
    path = session_path(name)
    if path.is_file():
        path.unlink()
        return True
    return False


def interactive_login(
    url: str,
    name: str,
    *,
    wait: Callable[[], Any] = input,
    timeout_ms: int = 30_000,
) -> Path:
    """Open a visible browser on ``url``; save the session once the user is done.

    The user signs in by hand (passwords, 2FA and SSO never pass through
    AgentCrawl), then presses Enter in the terminal.
    """
    validate_session_name(name)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "Playwright is not installed. Install agentcrawl[browser] and run "
            "python -m playwright install chromium.",
            error_type="config_error",
        ) from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            existing = session_path(name)
            context = browser.new_context(
                storage_state=str(existing) if existing.is_file() else None
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            wait()
            return save_storage_state(context, name)
        finally:
            browser.close()
