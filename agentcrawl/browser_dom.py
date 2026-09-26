"""Put content the browser renders but ``page.content()`` misses into the page.

``page.content()`` serializes the light DOM of the top document only, so two
kinds of rendered content never reached the Markdown:

- **Shadow DOM.** Web components (Lit, Stencil, Shoelace, Salesforce, many
  docs sites) render their text inside a shadow root. The serialized page shows
  an empty ``<my-card></my-card>``.
- **iframes.** Embedded documents (docs playgrounds, embedded forms, some
  article bodies) serialize as ``<iframe src=...>`` with nothing inside.

:func:`flatten_page` rewrites the live page just before it is read: every
open shadow root is copied into its host (slots filled with what was assigned
to them), and each visible iframe is replaced by a ``<div>`` holding its body.
It is best effort and bounded: a failure is reported, never fatal, because
the page as serialized is still a valid (if thinner) result. Closed shadow
roots are unreachable by design and stay as they are.
"""

from __future__ import annotations

import re
from typing import Any

MAX_IFRAMES = 10
MAX_IFRAME_DEPTH = 2
MAX_IFRAME_HTML_CHARS = 2_000_000
# Frames smaller than this in either direction are trackers, pixels or
# widgets, not content.
MIN_IFRAME_SIDE_PX = 50
_AD_FRAME_RE = re.compile(
    r"doubleclick\.net|googlesyndication\.com|adservice\.google|amazon-adsystem\.com|"
    r"/ads?/|recaptcha|hcaptcha\.com|challenges\.cloudflare\.com",
    re.I,
)

# Deepest hosts first, so a shadow root that contains other hosts is copied
# after those inner hosts were already flattened.
_FLATTEN_SHADOW_JS = """
(maxHosts) => {
  let flattened = 0;
  const hostsIn = (root) => Array.from(root.querySelectorAll('*')).filter((el) => el.shadowRoot);
  const flatten = (host) => {
    if (flattened >= maxHosts) return;
    const shadow = host.shadowRoot;
    for (const inner of hostsIn(shadow).reverse()) flatten(inner);
    for (const slot of shadow.querySelectorAll('slot')) {
      const assigned = slot.assignedNodes({ flatten: true });
      if (assigned.length) slot.replaceChildren(...assigned.map((node) => node.cloneNode(true)));
    }
    const copy = document.createElement('div');
    copy.setAttribute('data-agentcrawl-shadow-root', '');
    for (const node of shadow.childNodes) {
      if (node.nodeName !== 'STYLE' && node.nodeName !== 'SCRIPT') copy.appendChild(node.cloneNode(true));
    }
    // The shadow root keeps rendering the page; only the serialized light DOM
    // changes. Unslotted light children were never visible, so they go too.
    host.replaceChildren(copy);
    flattened += 1;
  };
  for (const host of hostsIn(document).reverse()) flatten(host);
  return flattened;
}
"""

_BODY_HTML_JS = "() => (document.body ? document.body.innerHTML : '')"

_REPLACE_IFRAME_JS = """
(frame, args) => {
  const box = document.createElement('div');
  box.setAttribute('data-agentcrawl-iframe', args.src || '');
  // Parse in an inert document and drop scripts and inline handlers, so the
  // frame's markup cannot run in the parent page once it is moved there.
  const doc = new DOMParser().parseFromString(args.html, 'text/html');
  doc.querySelectorAll('script, style, noscript').forEach((node) => node.remove());
  for (const el of doc.body.querySelectorAll('*')) {
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.toLowerCase().startsWith('on')) el.removeAttribute(attr.name);
    }
  }
  box.append(...Array.from(doc.body.childNodes).map((node) => document.importNode(node, true)));
  frame.replaceWith(box);
}
"""

MAX_SHADOW_HOSTS = 5_000


def flatten_page(page: Any, *, iframes: bool = True, shadow_dom: bool = True) -> dict[str, Any]:
    """Rewrite ``page`` so its serialization includes shadow roots and iframes.

    Returns a small report (``shadow_roots``, ``iframes``, ``iframes_skipped``
    and ``error`` when something went wrong) for the document metadata.
    """
    report: dict[str, Any] = {}
    try:
        main = page.main_frame
        if iframes:
            inlined, skipped = _inline_child_frames(main, shadow_dom=shadow_dom, depth=0)
            report["iframes"] = inlined
            if skipped:
                report["iframes_skipped"] = skipped
        if shadow_dom:
            report["shadow_roots"] = int(main.evaluate(_FLATTEN_SHADOW_JS, MAX_SHADOW_HOSTS) or 0)
    except Exception as exc:  # best effort: the unflattened page is still valid
        report["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return report


def _inline_child_frames(
    frame: Any, *, shadow_dom: bool, depth: int, budget: list[int] | None = None
) -> tuple[int, int]:
    budget = budget if budget is not None else [MAX_IFRAMES]
    inlined = skipped = 0
    for child in list(frame.child_frames):
        if budget[0] <= 0 or depth >= MAX_IFRAME_DEPTH:
            skipped += 1
            continue
        try:
            element = child.frame_element()
            src = child.url or ""
            box = element.bounding_box()
            if (
                _AD_FRAME_RE.search(src)
                or box is None
                or box["width"] < MIN_IFRAME_SIDE_PX
                or box["height"] < MIN_IFRAME_SIDE_PX
            ):
                skipped += 1
                continue
            # Nested frames first, so their content travels with this one.
            deeper, deeper_skipped = _inline_child_frames(
                child, shadow_dom=shadow_dom, depth=depth + 1, budget=budget
            )
            inlined += deeper
            skipped += deeper_skipped
            if shadow_dom:
                child.evaluate(_FLATTEN_SHADOW_JS, MAX_SHADOW_HOSTS)
            html = str(child.evaluate(_BODY_HTML_JS) or "")
            if not html.strip() or len(html) > MAX_IFRAME_HTML_CHARS:
                skipped += 1
                continue
            element.evaluate(_REPLACE_IFRAME_JS, {"src": src, "html": html})
            budget[0] -= 1
            inlined += 1
        except Exception:
            # A frame that navigated away or detached mid-read is skipped.
            skipped += 1
    return inlined, skipped
