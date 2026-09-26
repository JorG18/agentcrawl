"""Bounded browser actions run before a page is read (local Playwright only).

Some pages only show their content after a click ("Show more", a tab, a cookie
wall), a scroll (infinite feeds, virtualized lists) or typing into a search
box. ``browser_actions``
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
    # Scroll until the page stops growing (infinite feeds), bounded.
    "scroll_to_end": (frozenset(), frozenset({"max_scrolls", "settle_ms"})),
    # Scroll a virtualized list (rows are recycled as you scroll) and keep
    # every row seen, since the DOM only ever holds the visible window.
    "virtual_scroll": (frozenset({"selector"}), frozenset({"max_scrolls", "settle_ms"})),
}

DEFAULT_MAX_SCROLLS = 20
DEFAULT_SETTLE_MS = 600
MAX_SETTLE_MS = 5_000
MAX_VIRTUAL_ROWS = 20_000

_SCROLL_HEIGHT_JS = "() => (document.scrollingElement || document.body).scrollHeight"
_SCROLL_BOTTOM_JS = "() => { const el = document.scrollingElement || document.body; el.scrollTo(0, el.scrollHeight); }"

# Collect the container's current rows and scroll it one screen down. Returns
# the rows (outerHTML) and whether the scroll position moved.
_VIRTUAL_STEP_JS = """
(selector) => {
  const box = document.querySelector(selector);
  if (!box) throw new Error('no element matches ' + selector);
  // Spacer elements (no text) change height on every scroll; they are not rows.
  const rows = Array.from(box.children)
    .filter((row) => row.textContent.trim())
    .map((row) => row.outerHTML);
  const before = box.scrollTop;
  box.scrollTop = before + Math.max(box.clientHeight, 200);
  let moved = box.scrollTop !== before;
  // Scroll events are delivered on the next frame, which a headless page may
  // not render soon; dispatching one lets the list re-render right away.
  if (moved) box.dispatchEvent(new Event('scroll'));
  if (!moved) {
    const page = document.scrollingElement || document.body;
    const pageBefore = page.scrollTop;
    page.scrollTop = pageBefore + window.innerHeight;
    moved = page.scrollTop !== pageBefore;
    if (moved) window.dispatchEvent(new Event('scroll'));
  }
  return { rows, moved };
}
"""

_VIRTUAL_WRITE_JS = """
(args) => {
  const box = document.querySelector(args.selector);
  if (!box) return;
  // Row markup was read from this same page; it goes back as inert nodes.
  const doc = new DOMParser().parseFromString('<div>' + args.rows.join('') + '</div>', 'text/html');
  doc.querySelectorAll('script').forEach((node) => node.remove());
  // A listener-free copy of the container takes its place: shrinking the
  // original would clamp its scroll position, fire a scroll event and let the
  // list re-render its first screen before the page is read.
  const fresh = box.cloneNode(false);
  fresh.append(...Array.from(doc.body.firstChild.childNodes).map((n) => document.importNode(n, true)));
  fresh.style.height = 'auto';
  box.replaceWith(fresh);
}
"""


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
        _check_int(step, "max_scrolls", where, 1, MAX_SCROLLS)
        _check_int(step, "settle_ms", where, 0, MAX_SETTLE_MS)
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
        entry: dict[str, Any] = {"index": index, "type": kind, "ok": True}
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
            elif kind == "scroll_to_end":
                entry.update(_scroll_to_end(page, step))
            elif kind == "virtual_scroll":
                entry.update(_virtual_scroll(page, step))
        except Exception as exc:
            raise RuntimeError(f"browser action {index} ({kind}) failed: {exc}") from exc
        log.append(entry)
    return log


def _scroll_to_end(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    """Scroll to the bottom until the height holds for two rounds, or the cap."""
    max_scrolls = int(step.get("max_scrolls", DEFAULT_MAX_SCROLLS))
    settle_ms = int(step.get("settle_ms", DEFAULT_SETTLE_MS))
    height = page.evaluate(_SCROLL_HEIGHT_JS)
    unchanged = scrolls = 0
    while scrolls < max_scrolls and unchanged < 2:
        page.evaluate(_SCROLL_BOTTOM_JS)
        page.wait_for_timeout(settle_ms)
        scrolls += 1
        new_height = page.evaluate(_SCROLL_HEIGHT_JS)
        unchanged = unchanged + 1 if new_height <= height else 0
        height = max(height, new_height)
    return {"scrolls": scrolls, "reached_end": unchanged >= 2}


def _virtual_scroll(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    """Scroll a recycled list one screen at a time, keeping every row seen."""
    selector = step["selector"]
    max_scrolls = int(step.get("max_scrolls", DEFAULT_MAX_SCROLLS))
    settle_ms = int(step.get("settle_ms", DEFAULT_SETTLE_MS))
    rows: dict[str, None] = {}
    idle = scrolls = 0
    moved = True
    while scrolls < max_scrolls and idle < 2 and len(rows) < MAX_VIRTUAL_ROWS:
        result = page.evaluate(_VIRTUAL_STEP_JS, selector)
        before = len(rows)
        for row in result.get("rows", []):
            rows.setdefault(row, None)
        moved = bool(result.get("moved"))
        idle = idle + 1 if len(rows) == before or not moved else 0
        scrolls += 1
        if not moved:
            break
        page.wait_for_timeout(settle_ms)
    else:
        # The loop ended on the cap: read the rows the last scroll revealed.
        for row in page.evaluate(_VIRTUAL_STEP_JS, selector).get("rows", []):
            rows.setdefault(row, None)
    kept = list(rows)[:MAX_VIRTUAL_ROWS]
    page.evaluate(_VIRTUAL_WRITE_JS, {"selector": selector, "rows": kept})
    return {"scrolls": scrolls, "rows": len(kept), "reached_end": not moved or idle >= 2}
