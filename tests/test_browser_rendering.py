"""Real-browser checks for content ``page.content()`` alone misses.

Shadow DOM, iframes, infinite and virtualized scroll, and logged-in sessions
are read through the actual fetcher against a local HTTP server. The module
is skipped when Playwright or its Chromium is not installed (the default CI
job); the ``browser`` CI job installs both and runs it.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from agentcrawl import AgentCrawl

sync_api = pytest.importorskip("playwright.sync_api")


def _chromium_launches() -> bool:
    try:
        with sync_api.sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_launches(), reason="Chromium is not installed")

SHADOW = """<!doctype html><html><head><title>Shadow</title></head><body>
<h1>Component docs</h1>
<doc-card><span slot="title">Slotted title text</span></doc-card>
<script>
customElements.define('doc-card', class extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<style>p{color:red}</style><h2><slot name="title"></slot></h2>'
      + '<p>Text that only lives in the shadow root.</p><inner-note></inner-note>';
  }
});
customElements.define('inner-note', class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'}).innerHTML = '<p>Nested shadow paragraph.</p>';
  }
});
</script></body></html>"""

IFRAME_PAGE = """<!doctype html><html><head><title>Frames</title></head><body>
<h1>Embedding page</h1>
<iframe src="/frame" width="600" height="300"></iframe>
<iframe src="/frame-pixel" width="1" height="1"></iframe>
</body></html>"""

FRAME = """<!doctype html><html><body><h2>Inside the frame</h2>
<p>Paragraph that lives in the iframe document.</p>
<img src="/missing.png" onerror="document.title='owned'">
</body></html>"""

PIXEL = "<html><body><p>tracking pixel text</p></body></html>"

INFINITE = """<!doctype html><html><head><title>Feed</title></head><body>
<h1>Feed</h1><ul id="feed"></ul>
<script>
let batch = 0;
function load() {
  if (batch >= 6) return;
  const feed = document.getElementById('feed');
  for (let i = 0; i < 10; i++) {
    const li = document.createElement('li');
    li.textContent = 'Feed item ' + (batch * 10 + i);
    li.style.height = '120px';
    feed.appendChild(li);
  }
  batch += 1;
}
load();
window.addEventListener('scroll', () => {
  if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 50) setTimeout(load, 100);
});
</script></body></html>"""

VIRTUAL = """<!doctype html><html><head><title>Rows</title></head><body>
<h1>Rows</h1>
<div id="list" style="height:300px;overflow-y:auto"></div>
<script>
// A recycled list: only the rows in view exist in the DOM.
const list = document.getElementById('list');
const TOTAL = 120, ROW = 30;
function render() {
  const first = Math.floor(list.scrollTop / ROW);
  const rows = [];
  rows.push('<div style="height:' + first * ROW + 'px"></div>');
  for (let i = first; i < Math.min(TOTAL, first + 12); i++) {
    rows.push('<div class="row" style="height:' + ROW + 'px">Virtual row ' + i + '</div>');
  }
  rows.push('<div style="height:' + (TOTAL - first - 12) * ROW + 'px"></div>');
  list.innerHTML = rows.join('');
}
list.addEventListener('scroll', render);
render();
</script></body></html>"""

PRIVATE = "<html><head><title>Account</title></head><body><h1>Private dashboard</h1><p>Balance: 42 credits</p></body></html>"
LOGIN = "<html><head><title>Sign in</title></head><body><h1>Please sign in</h1><form><input name=user></form></body></html>"


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {
        "/shadow": SHADOW,
        "/frames": IFRAME_PAGE,
        "/frame": FRAME,
        "/frame-pixel": PIXEL,
        "/feed": INFINITE,
        "/virtual": VIRTUAL,
    }

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/account":
            logged_in = "sid=valid" in (self.headers.get("cookie") or "")
            body = PRIVATE if logged_in else LOGIN
            extra = [("set-cookie", "seen=1; Path=/")] if logged_in else []
        elif self.path in self.routes:
            body, extra = self.routes[self.path], []
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _crawler(**extra) -> AgentCrawl:
    return AgentCrawl(
        {
            "fetcher": "playwright",
            "allow_private_network": True,
            "respect_robots_txt": False,
            "network_idle": False,
            "timeout_ms": 15_000,
            **extra,
        }
    )


def test_shadow_dom_text_reaches_markdown(site) -> None:
    doc = _crawler().scrape(f"{site}/shadow")
    assert doc.ok, doc.errors
    assert "Text that only lives in the shadow root." in doc.markdown
    assert "Nested shadow paragraph." in doc.markdown
    assert "Slotted title text" in doc.markdown
    assert "color:red" not in doc.markdown
    assert doc.metadata["browser_dom"]["shadow_roots"] == 2


def test_shadow_dom_can_be_turned_off(site) -> None:
    doc = _crawler(browser_shadow_dom=False).scrape(f"{site}/shadow")
    assert "Text that only lives in the shadow root." not in doc.markdown


def test_iframe_content_is_inlined_without_running_its_handlers(site) -> None:
    doc = _crawler().scrape(f"{site}/frames")
    assert doc.ok, doc.errors
    assert "Paragraph that lives in the iframe document." in doc.markdown
    assert "tracking pixel text" not in doc.markdown
    assert doc.metadata["title"] != "owned"
    assert doc.metadata["browser_dom"]["iframes"] == 1


def test_scroll_to_end_loads_the_whole_feed(site) -> None:
    doc = _crawler(browser_actions=[{"type": "scroll_to_end", "settle_ms": 300}]).scrape(
        f"{site}/feed"
    )
    assert doc.ok, doc.errors
    assert "Feed item 59" in doc.markdown
    step = doc.metadata["browser_actions_log"][0]
    assert step["reached_end"] is True


def test_virtual_scroll_keeps_every_row(site) -> None:
    doc = _crawler(
        browser_actions=[
            {"type": "virtual_scroll", "selector": "#list", "max_scrolls": 30, "settle_ms": 50}
        ]
    ).scrape(f"{site}/virtual")
    assert doc.ok, doc.errors
    for row in (0, 60, 119):
        assert f"Virtual row {row}" in doc.markdown
    assert doc.markdown.count("Virtual row 5\n") <= 1


def test_saved_session_reads_the_private_page(site, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTCRAWL_SESSIONS_DIR", str(tmp_path))
    state = {
        "cookies": [
            {
                "name": "sid",
                "value": "valid",
                "domain": "127.0.0.1",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    (tmp_path / "work.json").write_text(json.dumps(state), encoding="utf-8")

    anonymous = _crawler().scrape(f"{site}/account")
    assert "Please sign in" in anonymous.markdown

    doc = _crawler(fetcher="http", browser_session="work").scrape(f"{site}/account")
    assert doc.ok, doc.errors
    assert "Balance: 42 credits" in doc.markdown
    assert doc.metadata["browser_session"] == "work"
    # The refreshed cookies were written back, owner-only.
    saved = json.loads((tmp_path / "work.json").read_text("utf-8"))
    assert {cookie["name"] for cookie in saved["cookies"]} >= {"sid", "seen"}
    assert (tmp_path / "work.json").stat().st_mode & 0o077 == 0
    assert "valid" not in json.dumps(doc.metadata)
