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
