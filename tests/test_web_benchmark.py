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
    assert not is_candidate("javhd.today")
    assert is_candidate("wikipedia.org")


def test_block_pages_are_not_content() -> None:
    cloudflare = (
        "# carid.com\n## Performing security verification\nThis website uses a security service "
        "to protect against malicious bots. This page is displayed while the website verifies "
        "you are not a bot.\n## Verification successful. Waiting for carid.com to respond"
    )
    assert classify({"markdown": cloudflare}) == "junk"
    assert classify({"markdown": ARTICLE, "error": "Blocked by anti-bot protection"}) == "failed"


def test_robot_block_notice_is_junk() -> None:
    nyt = "You have been blocked from The New York Times because we suspect that you're a robot."
    assert classify({"markdown": nyt * 3}) == "junk"


def test_tool_limited_to_part_of_the_sample_is_judged_on_what_it_ran() -> None:
    sample = {
        "seed": 1,
        "pages": [
            {"id": f"p{i}", "url": f"https://s{i}.org/", "stratum": "1-1000", "kind": "homepage"}
            for i in range(4)
        ],
    }
    full = {f"p{i}": {"id": f"p{i}", "tool": "full", "markdown": ARTICLE} for i in range(4)}
    partial = {"p0": {"id": "p0", "tool": "partial", "markdown": ARTICLE}}
    partial.update(
        {
            f"p{i}": {"id": f"p{i}", "tool": "partial", "skipped": "beyond --limit"}
            for i in (1, 2, 3)
        }
    )
    report = analyse(sample, {"full": full, "partial": partial})
    row = {r["tool"]: r for r in report["summary"]}["partial"]
    assert (row["pages"], row["content"], row["missed_reachable"]) == (1, 1, 0)
    assert row["consensus_recall"] == 1.0
    assert report["content_rate_by_rank"]["1-1000"]["partial"] == 1.0


def test_hard_categories_match_the_pages_they_name() -> None:
    from benchmarks.web.sample import HARD_CATEGORIES

    def matches(category: str, path: str) -> bool:
        return bool(HARD_CATEGORIES[category]["pattern"].search(path))

    assert matches("product", "/dp/B0C1234567")
    assert matches("news", "/2025/03/14/some-story")
    assert matches("forum", "/t/how-to-install/1234")
    assert matches("pdf", "/files/report.pdf")
    assert not matches("product", "/about-us")
    gov = HARD_CATEGORIES["government"]["domains"]
    assert gov.search("gov.uk") and gov.search("usa.gov") and gov.search("gob.mx")
    assert not gov.search("google.com")


def test_hard_block_is_reported_apart(tmp_path) -> None:
    import gzip as gz
    import json as js

    from benchmarks.web import report as rep

    sample = {
        "seed": 1,
        "pages": [
            {
                "id": "r0",
                "url": "https://a.org/",
                "stratum": "1-1000",
                "kind": "homepage",
                "block": "random",
            },
            {
                "id": "h0",
                "url": "https://b.gov/x.pdf",
                "stratum": "hard",
                "kind": "pdf",
                "block": "hard",
            },
        ],
    }
    (tmp_path / "s.json").write_text(js.dumps(sample))
    with gz.open(tmp_path / "t.jsonl.gz", "wt") as handle:
        for pid in ("r0", "h0"):
            handle.write(js.dumps({"id": pid, "tool": "agentcrawl", "markdown": ARTICLE}) + "\n")
    out = tmp_path / "rep"
    rep.main(
        [str(tmp_path / "t.jsonl.gz"), "--sample", str(tmp_path / "s.json"), "--out", str(out)]
    )
    data = js.loads((tmp_path / "rep.json").read_text())
    assert data["pages"] == 1 and data["hard_block"]["pages"] == 1
    assert "# Hard block: 1 pages" in (tmp_path / "rep.md").read_text()
