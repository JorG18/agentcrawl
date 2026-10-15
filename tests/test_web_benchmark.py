"""The web-sample report classifies every tool's output the same way."""

from __future__ import annotations

import pytest

import random

from benchmarks.web import sample
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


def test_recaptcha_footer_is_not_a_block_page() -> None:
    page = ARTICLE + " This site is protected by reCAPTCHA and the Google Privacy Policy apply."
    assert classify({"markdown": page}) == "content"
    assert classify({"markdown": "Please solve the CAPTCHA to continue. " * 3}) == "junk"


def test_error_status_page_is_not_content() -> None:
    assert classify({"markdown": ARTICLE, "status_code": 404}) == "failed"
    assert classify({"markdown": ARTICLE, "status_code": 200}) == "content"


def test_local_tools_share_one_wall_clock() -> None:
    slow = {"markdown": ARTICLE, "seconds": 76}  # past the 60 s cap + 15 s grace
    assert classify({**slow, "tool": "scrapling"}) == "failed"
    assert classify({**slow, "tool": "agentcrawl"}) == "failed"
    assert classify({**slow, "tool": "agentcrawl", "seconds": 74}) == "content"
    # A hosted API's time includes our rate-limit waits.
    assert classify({**slow, "tool": "firecrawl"}) == "content"


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


def test_each_hard_category_gets_its_own_time(monkeypatch) -> None:
    # Products never match; with one shared budget they used to spend all of
    # it and every later category came out empty.
    monkeypatch.setattr(sample.time, "sleep", lambda seconds: None)
    clock = {"now": 0.0}
    monkeypatch.setattr(sample.time, "monotonic", lambda: clock["now"])

    def fake_inner(domain, index_api, rng, *, mime, pattern, hint, deadline):
        clock["now"] += 1.0
        if pattern is not None and pattern.pattern.startswith("/(?:products"):
            return None
        return (f"https://{domain}/news/2024/story", "sitemap")

    monkeypatch.setattr(sample, "inner_page", fake_inner)
    ranked = [(rank, f"site{rank}.gov") for rank in range(1, 400)]
    pages = sample.hard_block(10, 1, ranked, "https://index", workers=1, deadline=100.0)
    kinds = {page["kind"] for page in pages}
    assert "product" not in kinds
    assert {"news", "forum", "government", "pdf"} <= kinds


def test_common_crawl_is_skipped_once_it_is_down(monkeypatch) -> None:
    monkeypatch.setattr(sample.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sample, "CC_HEALTH", sample._IndexHealth(limit=3))
    calls: list[str] = []

    def down(url, *, timeout=30.0):
        calls.append(url)
        raise OSError("503")

    monkeypatch.setattr(sample, "_get", down)
    rng = random.Random(1)
    for domain in ("a.com", "b.com", "c.com"):
        assert sample._cc_page(domain, "https://index", rng) is None
    assert len(calls) == 3  # three failures in a row, then no more requests
    assert sample.CC_HEALTH.skipped >= 2


def test_sitemap_children_matching_the_category_are_read_first(monkeypatch) -> None:
    children = [f"https://shop.com/sitemap-pages-{i}.xml" for i in range(8)]
    listings = {
        "https://shop.com/robots.txt": "Sitemap: https://shop.com/sitemap.xml",
        "https://shop.com/sitemap.xml": "".join(
            f"<loc>{url}</loc>" for url in children + ["https://shop.com/sitemap-products.xml"]
        ),
        "https://shop.com/sitemap-products.xml": "<loc>https://shop.com/product/blue-kettle</loc>",
    }
    read: list[str] = []

    def fetch(url, *, limit=0):
        read.append(url)
        if url not in listings:
            return "<loc>https://shop.com/about/team</loc>"
        return listings[url]

    monkeypatch.setattr(sample, "_fetch_text", fetch)
    rule = sample.HARD_CATEGORIES["product"]
    url = sample.sitemap_page(
        "shop.com", random.Random(3), pattern=rule["pattern"], hint=rule["sitemap_hint"]
    )
    assert url == "https://shop.com/product/blue-kettle"
    assert read[2] == "https://shop.com/sitemap-products.xml"


def test_a_sitemap_url_list_is_content_not_thin() -> None:
    from benchmarks.web.report import classify

    urls = "\n".join(f"- https://example.com/page/{n}" for n in range(6))
    assert classify({"markdown": urls}) == "content"
    assert classify({"markdown": "- https://example.com/a"}) == "thin"


def test_cap_applies_to_every_tool_alike() -> None:
    for tool in ("agentcrawl", "agentcrawl-stealth", "scrapling", "crawl4ai"):
        late = {"tool": tool, "markdown": ARTICLE, "seconds": 76, "page_timeout": 60}
        in_time = {**late, "seconds": 74}
        assert classify(late) == "failed", tool
        assert classify(in_time) == "content", tool


def test_uncapped_run_keeps_slow_pages() -> None:
    slow = {"tool": "scrapling", "markdown": ARTICLE, "seconds": 120, "page_timeout": 180}
    assert classify(slow) == "content"
    # Results from before the field existed were run with a 60 s cap.
    assert classify({"tool": "scrapling", "markdown": ARTICLE, "seconds": 120}) == "failed"


def test_variants_are_local() -> None:
    from benchmarks.web.report import is_local

    assert is_local("agentcrawl-stealth") is True
    assert is_local("crawl4ai") is True
    assert is_local("firecrawl") is False


def test_hosted_time_is_not_capped_by_the_harness() -> None:
    # Hosted seconds include our own 429 waits; the API got the cap as its timeout.
    hosted = {"tool": "firecrawl", "markdown": ARTICLE, "seconds": 200, "page_timeout": 60}
    assert classify(hosted) == "content"


# -- aggregate: pool runs and test differences -------------------------------------
def _run(contents: dict[str, dict[str, bool]]) -> dict:
    """{tool: {page: has_content}} → the shape report.load returns."""
    return {
        tool: {
            page: {"tool": tool, "id": page, "markdown": ARTICLE if ok else ""}
            for page, ok in pages.items()
        }
        for tool, pages in contents.items()
    }


def test_pairs_are_per_page_and_run() -> None:
    from benchmarks.web.aggregate import pooled

    run1 = _run({"a": {"x": True, "y": True}, "b": {"x": False, "y": True}})
    run2 = _run({"a": {"x": False, "y": True}, "b": {"x": True, "y": True}})

    summary = pooled([run1, run2])

    assert summary["tools"]["a"]["content_per_run"] == [2, 1]
    assert summary["tools"]["a"]["content_total"] == 3
    pair = summary["pairs"]["a vs b"]
    assert (pair["a_only"], pair["b_only"]) == (1, 1)
    assert pair["band"] == 2  # differences per run: +1 and -1


def test_mcnemar_exact() -> None:
    from benchmarks.web.aggregate import mcnemar_p

    assert mcnemar_p(10, 2) == pytest.approx(0.0386, abs=1e-4)
    assert mcnemar_p(0, 0) == 1.0


def test_tie_is_written_as_a_tie() -> None:
    from benchmarks.web.aggregate import markdown, pooled

    run = _run({"a": {"x": True, "y": False}, "b": {"x": False, "y": True}})
    text = markdown(pooled([run]))
    assert "tie within noise" in text
