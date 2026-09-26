"""Bounded browser actions run before a page is read (local Playwright only).

Some pages only show their content after a click ("Show more", a tab, a cookie
wall), a scroll (infinite lists) or typing into a search box. ``browser_actions``
is a short list of steps run in order after navigation and before the page is
captured. It is deliberately small and bounded: a handful of step types, at
most :data:`MAX_ACTIONS` steps, per-step limits, and every step shares the
fetch's ``timeout_ms``. It is not a scripting language; anything that needs
branching belongs in a real browser-automation tool.

Every step's outcome is reported (``browser_actions_log`` in the document
metadata), and a failing step fails the scrape with the step named, instead of
returning whatever half-loaded page was left.
"""

from __future__ import annotations

from typing import Any

MAX_ACTIONS = 25
MAX_WAIT_MS = 30_000
MAX_SCROLLS = 50
MAX_TEXT_CHARS = 10_000

# Step type -> (required keys, optional keys)
_SPEC: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "wait_for": (frozenset({"selector"}), frozenset({"timeout_ms"})),
    "click": (frozenset({"selector"}), frozenset()),
    "type": (frozenset({"selector", "text"}), frozenset()),
    "press": (frozenset({"key"}), frozenset({"selector"})),
    "scroll": (frozenset(), frozenset({"times"})),
    "wait": (frozenset({"ms"}), frozenset()),
}


def validate_actions(value: Any) -> tuple[dict[str, Any], ...]:
    """Check a ``browser_actions`` list, raising ``ValueError`` with the bad step."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, (list, tuple)):
        raise ValueError("browser_actions must be a list of steps")
    if len(value) > MAX_ACTIONS:
        raise ValueError(f"browser_actions allows at most {MAX_ACTIONS} steps, got {len(value)}")
    checked: list[dict[str, Any]] = []
    for index, step in enumerate(value):
        where = f"browser_actions[{index}]"
        if not isinstance(step, dict):
            raise ValueError(f"{where} must be an object like {{'type': 'click', ...}}")
        kind = step.get("type")
        if kind not in _SPEC:
            raise ValueError(f"{where}.type must be one of {', '.join(_SPEC)}, got {kind!r}")
        required, optional = _SPEC[kind]
        keys = set(step) - {"type"}
        missing = sorted(required - keys)
        unknown = sorted(keys - required - optional)
        if missing:
            raise ValueError(f"{where} ({kind}) is missing {', '.join(missing)}")
        if unknown:
            raise ValueError(f"{where} ({kind}) does not accept {', '.join(unknown)}")
        for key in ("selector", "text", "key"):
            if key in step and (not isinstance(step[key], str) or not step[key]):
                raise ValueError(f"{where}.{key} must be a non-empty string")
        if len(step.get("text", "")) > MAX_TEXT_CHARS:
            raise ValueError(f"{where}.text is longer than {MAX_TEXT_CHARS} characters")
        _check_int(step, "timeout_ms", where, 1, MAX_WAIT_MS)
        _check_int(step, "ms", where, 0, MAX_WAIT_MS)
        _check_int(step, "times", where, 1, MAX_SCROLLS)
        checked.append(dict(step))
    return tuple(checked)


def _check_int(step: dict[str, Any], key: str, where: str, low: int, high: int) -> None:
    if key not in step:
        return
    value = step[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{where}.{key} must be an integer between {low} and {high}")


def run_actions(page: Any, actions: tuple[dict[str, Any], ...], timeout_ms: int) -> list[dict]:
    """Run ``actions`` on a Playwright ``page``; return one log entry per step.

    Raises ``RuntimeError`` naming the failed step; the caller turns it into a
    ``FetchError`` so the scrape fails honestly.
    """
    log: list[dict[str, Any]] = []
    for index, step in enumerate(actions):
        kind = step["type"]
        step_timeout = min(int(step.get("timeout_ms", timeout_ms)), timeout_ms)
        try:
            if kind == "wait_for":
                page.wait_for_selector(step["selector"], timeout=step_timeout)
            elif kind == "click":
                page.click(step["selector"], timeout=step_timeout)
            elif kind == "type":
                page.fill(step["selector"], step["text"], timeout=step_timeout)
            elif kind == "press":
                if "selector" in step:
                    page.press(step["selector"], step["key"], timeout=step_timeout)
                else:
                    page.keyboard.press(step["key"])
            elif kind == "scroll":
                for _ in range(int(step.get("times", 1))):
                    page.mouse.wheel(0, 10_000)
                    page.wait_for_timeout(250)
            elif kind == "wait":
                page.wait_for_timeout(min(int(step["ms"]), timeout_ms))
        except Exception as exc:
            raise RuntimeError(f"browser action {index} ({kind}) failed: {exc}") from exc
        log.append({"index": index, "type": kind, "ok": True})
    return log
