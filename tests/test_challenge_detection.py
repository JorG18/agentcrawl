"""Challenge detection on real interstitials and on real pages that mention them.

The first detector flagged any page containing "client challenge" (including
this project's own GitHub page) and let HTTP-200 Cloudflare, DataDome and
PerimeterX interstitials through as content. These fixtures pin both sides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcrawl import AgentCrawl
from agentcrawl.crawler import _blocked_page_reason, _challenge_verdict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "challenges"

CHALLENGES = [
    "fastly_pypi.html",
    "cloudflare_200.html",
    "cloudflare_turnstile.html",
    "datadome.html",
    "perimeterx.html",
]
REAL_PAGES = [
    "article_about_challenges.html",
    "readme_mentions_challenge.html",
]


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CHALLENGES)
def test_interstitials_are_detected(name: str) -> None:
    verdict = _challenge_verdict(_read(name))
    assert verdict.is_challenge, name
    assert verdict.signals


@pytest.mark.parametrize("name", REAL_PAGES)
def test_pages_that_talk_about_challenges_are_content(name: str) -> None:
    assert _blocked_page_reason(_read(name)) == ""


def test_quality_fixtures_are_never_flagged() -> None:
    quality = Path(__file__).resolve().parent / "fixtures" / "quality"
    flagged = [
        path.name
        for path in sorted(quality.glob("*.html"))
        if _blocked_page_reason(path.read_text(encoding="utf-8"))
    ]
    assert flagged == []


def test_vendor_widget_on_a_normal_form_is_not_a_challenge() -> None:
    html = (
        "<html><head><title>Sign in</title></head><body><form><label>Email</label>"
        "<input name=email><div class='g-recaptcha' data-sitekey='x'></div>"
        "<button>Sign in</button></form></body></html>"
    )
    assert _blocked_page_reason(html) == ""


def test_access_denied_title_alone_is_not_a_challenge() -> None:
    html = (
        "<html><head><title>Access denied</title></head><body><h1>Access denied</h1>"
        "<p>You do not have permission to view this project. Ask an admin for access.</p>"
        "</body></html>"
    )
    assert _blocked_page_reason(html) == ""


def test_challenge_scrape_reports_signals(monkeypatch) -> None:
    html = _read("cloudflare_turnstile.html")
    monkeypatch.setattr(
        "agentcrawl.crawler.fetch_source",
        lambda source, config: (html, {"fetcher": "http", "final_url": source}),
    )
    doc = AgentCrawl({"fetcher": "http", "browser_fallback": False}).scrape(
        "https://shop.example.org/"
    )
    assert doc.metadata["error_type"] == "client_challenge"
    assert doc.markdown == ""
    assert "title: just a moment" in doc.metadata["challenge_signals"]


def test_real_page_mentioning_challenges_scrapes(monkeypatch) -> None:
    html = _read("article_about_challenges.html")
    monkeypatch.setattr(
        "agentcrawl.crawler.fetch_source",
        lambda source, config: (html, {"fetcher": "http", "final_url": source}),
    )
    doc = AgentCrawl({"fetcher": "http", "browser_fallback": False}).scrape(
        "https://blog.example.org/bot-challenges"
    )
    assert doc.ok
    assert "How bot challenges work" in doc.markdown


def test_robot_block_notice_is_a_challenge() -> None:
    # DataDome's block page on nytimes.com, served to the browser fallback
    # (found by the web-sample benchmark).
    html = (
        "<html><head><title>nytimes.com</title></head><body><p>You have been blocked from "
        "The New York Times because we suspect that you're a robot.</p><p>Why am I seeing "
        "this? You are browsing much faster than is typical of a human being.</p></body></html>"
    )
    assert _blocked_page_reason(html) == "blocked as a robot"
