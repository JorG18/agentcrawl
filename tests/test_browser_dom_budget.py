"""flatten_page stops at the page deadline (P1: apnews, 133 frames, took 5-10 s
after the budget was already spent)."""

from __future__ import annotations

import time

from agentcrawl.browser_dom import flatten_page


class SlowFrame:
    def __init__(self, children=()):
        self.child_frames = list(children)
        self.url = "https://example.com/frame"

    def frame_element(self):
        return SlowElement()

    def evaluate(self, _js, _arg=None):
        time.sleep(0.1)
        return "<p>frame text</p>"


class SlowElement:
    def bounding_box(self):
        return {"width": 400, "height": 300}

    def evaluate(self, _js, _arg=None):
        return None


class FakePage:
    def __init__(self, frames: int):
        self.main_frame = SlowFrame([SlowFrame() for _ in range(frames)])


def test_flatten_stops_at_the_deadline():
    start = time.monotonic()
    report = flatten_page(FakePage(frames=40), deadline=start + 0.5)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # 40 frames x ~0.3 s would be ~12 s
    assert report["budget_spent"] is True
    assert report["iframes_skipped"] > 0


def test_flatten_without_deadline_reads_every_frame():
    report = flatten_page(FakePage(frames=3))
    assert report["iframes"] == 3
    assert "budget_spent" not in report


def test_a_frame_that_landed_on_a_refused_url_is_not_inlined():
    # SSRF: outside strict mode an iframe's redirect hops are not checked, so
    # the frame's final URL is checked before its content joins the page.
    class Guard:
        enforcing = True
        strict = False

        def check(self, url):
            return "private address" if "internal" in url else None

    page = FakePage(frames=2)
    page.main_frame.child_frames[1].url = "http://internal.example/secret"

    report = flatten_page(page, guard=Guard())

    assert report["iframes"] == 1 and report["iframes_skipped"] == 1


def test_a_frame_that_navigates_to_a_refused_url_while_read_is_not_inlined():
    class Guard:
        enforcing = True
        strict = False

        def check(self, url):
            return "private address" if "internal" in url else None

    class MovingFrame(SlowFrame):
        def evaluate(self, _js, _arg=None):
            self.url = "http://internal.example/secret"  # navigates during the read
            return "<p>secret</p>"

    page = FakePage(frames=0)
    page.main_frame.child_frames = [MovingFrame()]

    report = flatten_page(page, guard=Guard())

    assert report.get("iframes", 0) == 0 and report["iframes_skipped"] == 1


def test_dropping_a_frame_after_its_read_does_not_count_its_nested_frames():
    class Guard:
        enforcing = True
        strict = False

        def check(self, url):
            return "private address" if "internal" in url else None

    class MovingFrame(SlowFrame):
        def evaluate(self, _js, _arg=None):
            self.url = "http://internal.example/secret"
            return "<p>secret</p>"

    page = FakePage(frames=0)
    page.main_frame.child_frames = [MovingFrame([SlowFrame(), SlowFrame()])]

    report = flatten_page(page, guard=Guard())

    assert report.get("iframes", 0) == 0
