"""The web-sample report classifies every tool's output the same way."""

from __future__ import annotations

from benchmarks.web.report import analyse, classify, sentences
from benchmarks.web.sample import is_candidate

ARTICLE = " ".join(
    f"Sentence number {i} explains how the product works in detail." for i in range(20)
)


def test_buckets() -> None:
    assert classify({"skipped": "no key"}) == "skipped"
    assert classify({"error": "HTTP 403", "markdown": ""}) == "failed"
    assert classify({"markdown": "Just a moment... Verifying you are human."}) == "junk"
    assert classify({"markdown": "# Home\n\n[Login](https://x.org/login)"}) == "thin"
    assert classify({"markdown": ARTICLE}) == "content"
    # A long article that mentions captchas is still content.
    assert (
        classify({"markdown": ARTICLE * 5 + " We removed the captcha from sign-up."}) == "content"
    )


def test_sentences_ignore_markup_and_short_fragments() -> None:
    found = sentences(
        "## Title\n\nThe **quick** brown fox jumps over the [lazy dog](https://d.org) today.\n\nShort."
    )
    assert found == {"the quick brown fox jumps over the lazy dog today."}


def test_consensus_recall_and_gaps() -> None:
    sample = {
        "seed": 1,
        "pages": [
            {"id": "p1", "url": "https://a.org/", "stratum": "1-1000", "kind": "homepage"},
            {"id": "p2", "url": "https://b.org/x", "stratum": "1-1000", "kind": "inner"},
        ],
    }
    half = ". ".join(ARTICLE.split(". ")[:10]) + "."
    by_tool = {
        "agentcrawl": {
            "p1": {"id": "p1", "tool": "agentcrawl", "markdown": ARTICLE},
            "p2": {
                "id": "p2",
                "tool": "agentcrawl",
                "markdown": "",
                "error": "blocked",
                "error_type": "client_challenge",
            },
        },
        "other": {
            "p1": {"id": "p1", "tool": "other", "markdown": half},
            "p2": {"id": "p2", "tool": "other", "markdown": ARTICLE},
        },
        "hosted": {
            "p1": {"id": "p1", "tool": "hosted", "skipped": "no key"},
            "p2": {"id": "p2", "tool": "hosted", "skipped": "no key"},
        },
    }
    report = analyse(sample, by_tool)
    rows = {row["tool"]: row for row in report["summary"]}
    assert report["skipped_tools"] == ["hosted"]
    assert rows["agentcrawl"]["consensus_recall"] == 1.0
    assert rows["other"]["consensus_recall"] == 1.0
    assert rows["agentcrawl"]["missed_reachable"] == 1
    assert report["agentcrawl_gaps"][0]["error_type"] == "client_challenge"


def test_infrastructure_and_adult_domains_are_not_sampled() -> None:
    assert not is_candidate("fonts.googleapis.com")
    assert not is_candidate("d1.cloudfront.net")
    assert not is_candidate("xvideos.com")
    assert is_candidate("wikipedia.org")
