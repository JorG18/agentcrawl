"""Decide whether a fetched page is a bot challenge instead of content.

The first version searched the visible text for three phrases. That failed in
both directions: any real page that *talks about* challenges (a scraping blog,
a security write-up, this project's own README on GitHub) was thrown away,
while real Cloudflare, DataDome or PerimeterX interstitials served with HTTP
200 came back as "content".

A challenge page has a shape, not just a phrase:

- a known interstitial ``<title>`` ("Just a moment...") is enough on its own;
- a vendor's challenge script or element (``/cdn-cgi/challenge-platform/``,
  ``captcha-delivery.com``, ``px-captcha``...) counts only when the page has
  almost no readable text, because the same widgets also sit on normal login
  or comment forms;
- challenge wording ("verifying you are human", "client challenge") counts
  only on a short page, never on an article that merely mentions it.

The result lists the signals that fired so the caller can say *why*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Readable text above this size is a real page, whatever else it contains.
# Interstitials are a heading, a sentence or two and a widget.
SHORT_PAGE_CHARS = 1500
VERY_SHORT_PAGE_CHARS = 600

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)

# Titles that only interstitials use. Matched against the whole title.
_CHALLENGE_TITLES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("title: just a moment", re.compile(r"^just a moment\.*$", re.I)),
    (
        "title: attention required (cloudflare)",
        re.compile(r"^attention required!? \| cloudflare$", re.I),
    ),
    ("title: client challenge", re.compile(r"^client challenge$", re.I)),
    ("title: ddos-guard", re.compile(r"^ddos-guard$", re.I)),
    ("title: pardon our interruption", re.compile(r"^pardon our interruption\.*$", re.I)),
    ("title: are you a robot", re.compile(r"^are you a (?:robot|human)\??$", re.I)),
    (
        "title: human verification",
        re.compile(r"^(?:human verification|verify you are human)$", re.I),
    ),
)

# Titles that are challenge-like but also used by ordinary error pages; they
# count only on a short page.
_SHORT_PAGE_TITLES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("title: access denied", re.compile(r"^access (?:to this page has been )?denied", re.I)),
    ("title: security check", re.compile(r"^(?:security check|one more step)", re.I)),
    ("title: captcha", re.compile(r"captcha", re.I)),
)

# Vendor fingerprints in the raw HTML (scripts, ids, form actions).
_VENDOR_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cloudflare challenge platform", re.compile(r"/cdn-cgi/challenge-platform/", re.I)),
    ("cloudflare turnstile", re.compile(r"challenges\.cloudflare\.com", re.I)),
    ("datadome", re.compile(r"captcha-delivery\.com|geo\.captcha-delivery|datadome", re.I)),
    ("perimeterx", re.compile(r"px-captcha|perimeterx|/_px\d*/|human security", re.I)),
    ("imperva/incapsula", re.compile(r"_incapsula_resource|incapsula incident", re.I)),
    ("aws waf", re.compile(r"awswaf|aws-waf-token|challenge\.js", re.I)),
    ("radware", re.compile(r"perfdrive\.com|shieldsquare", re.I)),
    ("ddos-guard", re.compile(r"ddos-guard", re.I)),
    ("fastly client challenge", re.compile(r"/_fs-ch-", re.I)),
    ("sucuri", re.compile(r"sucuri website firewall", re.I)),
    ("recaptcha/hcaptcha", re.compile(r"g-recaptcha|h-captcha|hcaptcha\.com", re.I)),
)

# Challenge wording in the readable text.
_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("client challenge", re.compile(r"client challenge", re.I)),
    (
        "required part of this site couldn't load",
        re.compile(r"required part of this site (?:couldn[’']t|could not) load", re.I),
    ),
    ("disable any ad blockers", re.compile(r"disable (?:any|your) ad ?blockers?", re.I)),
    ("verifying you are human", re.compile(r"verify(?:ing)? (?:that )?you are (?:a )?human", re.I)),
    (
        "checking your browser",
        re.compile(r"checking (?:if the site connection is secure|your browser)", re.I),
    ),
    (
        "enable javascript and cookies",
        re.compile(r"enable javascript and cookies to continue", re.I),
    ),
    ("press and hold", re.compile(r"press (?:&|and) hold", re.I)),
    ("unusual traffic", re.compile(r"unusual traffic from your (?:computer )?network", re.I)),
    (
        "not a robot",
        re.compile(r"(?:confirm|prove) (?:that )?you(?:'re| are) not a (?:robot|bot)", re.I),
    ),
)


@dataclass(slots=True)
class ChallengeVerdict:
    signals: list[str] = field(default_factory=list)
    text_chars: int = 0

    @property
    def is_challenge(self) -> bool:
        return bool(self.signals)

    @property
    def reason(self) -> str:
        return self.signals[0] if self.signals else ""


def page_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def detect_challenge(html: str, visible_text: str) -> ChallengeVerdict:
    """Classify a page from its raw HTML and its readable text.

    ``visible_text`` is the page without scripts, styles or tags, one block per
    line, and without cookie-banner lines (see ``crawler._html_to_plain_text``).
    """
    text_chars = len(re.sub(r"\s+", "", visible_text or ""))
    verdict = ChallengeVerdict(text_chars=text_chars)
    short = text_chars <= SHORT_PAGE_CHARS
    title = page_title(html)

    for label, pattern in _CHALLENGE_TITLES:
        if title and pattern.search(title):
            verdict.signals.append(label)
    if not short:
        # A real page, even if its title is unusual: only an exact interstitial
        # title can still flag it (some interstitials pad themselves with
        # hidden text, and no real page is titled "Just a moment...").
        return verdict

    titles = [label for label, pattern in _SHORT_PAGE_TITLES if title and pattern.search(title)]
    vendors = [
        f"vendor: {label}" for label, pattern in _VENDOR_MARKERS if pattern.search(html or "")
    ]
    phrases = [label for label, pattern in _PHRASES if pattern.search(visible_text or "")]
    kinds = sum(1 for group in (titles, vendors, phrases) if group)
    # Wording alone is enough only on a very short page (an interstitial is a
    # sentence or two); otherwise two independent kinds of evidence are needed,
    # because each one alone also shows up on ordinary pages: "Access denied"
    # error pages, reCAPTCHA on a login form, a short post about bot checks.
    if kinds >= 2 or (phrases and text_chars <= VERY_SHORT_PAGE_CHARS):
        verdict.signals.extend(titles + vendors + phrases)
    return verdict
