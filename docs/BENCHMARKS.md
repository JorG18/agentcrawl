# Benchmarks

How AgentCrawl compares with Crawl4AI, Scrapling and Firecrawl on real websites, measured in October 2026. Every number below comes from a GitHub Actions run or a command in this repository, and the summaries and samples behind it are committed in [`benchmarks/results/2026-10-fresh/`](../benchmarks/results/2026-10-fresh/) and [`benchmarks/results/0.6.0/`](../benchmarks/results/0.6.0/). Firecrawl is only in the 0.6.0 numbers.

## Fresh sample (October 2026)

Before the next release we drew a new sample, so that the coverage numbers no longer come from pages the engine was tuned on. The rules (hypothesis, test and the wording for each outcome) were written down before the sample was drawn. Measured on 2026-10-24 at commit `9737c5f`, which is 0.6.0 plus fixes and before the extraction fixes of PR #59. Summaries and the sample are in [`benchmarks/results/2026-10-fresh/`](../benchmarks/results/2026-10-fresh/).

- **Sample:** 400 sites drawn with seed 20261024 from the Tranco top 100,000 by the same script and strata as 0.6.0: 323 homepages and 77 inner pages (70 from sitemaps, 7 from Common Crawl index `CC-MAIN-2026-43`).
- **Runs:** two runs on the fresh sample plus one control run on the 0.6.0 sample, all with a 60 s cap and the same day, AgentCrawl with its stealth extra, Scrapling 0.5, Crawl4AI 0.9.5. All three tools ran on the same runner at the same time. Firecrawl was not run.
- **Test:** an exact McNemar test on the pages where only one of two tools got content, **in each run separately**. A difference counts only if both runs give p < 0.05 in the same direction, because the two runs repeat the same pages and pooling them would shrink p.

Pages with readable content (pages tried in brackets; the fresh sample pools its two runs, the 0.6.0 sample is one control run):

| tool | fresh sample | 0.6.0 sample, same day and versions |
|---|---|---|
| Scrapling | 63.6 % (800) | 64.5 % (400) |
| AgentCrawl | 60.8 % (800) | 63.5 % (400) |
| Crawl4AI | 52.4 % (800) | 54.8 % (400) |

| pair | run 1: only AgentCrawl / only other | p | run 2 | p | verdict |
|---|---|---|---|---|---|
| vs Scrapling | 21 / 32 | 0.17 | 19 / 31 | 0.12 | no significant difference (this sample cannot detect gaps under about 3 points) |
| vs Crawl4AI | 48 / 18 | < 0.001 | 52 / 15 | < 0.001 | AgentCrawl reads more pages |

On the fresh sample the gap to Scrapling is −2.9 points. On the 0.6.0 sample, measured the same day with the same versions, it is −1.0 point. The rule written before the draw counts that 1.9-point difference as no evidence of a change; this design cannot detect shifts under about 5 points, so it does not rule them out either. Scrapling read more pages in both fresh runs, but neither run reaches significance. By page kind (from the run artifacts, not in the committed summaries), the 77 inner pages are close (66 and 65 for AgentCrawl, 66 and 68 for Scrapling) and the gap comes from homepages.

The 0.6.0 results below are kept as published. Their 400-site sample was also used to tune the engine, so they are labelled as such.

## 0.6.0 summary (sample also used for tuning)

- **Pages read (400 random real sites, two runs per time cap):** AgentCrawl, Firecrawl and Scrapling show no significant difference, at about 64–66 % of pages with readable content. AgentCrawl reads more pages than Crawl4AI (p < 0.001).
- **What comes back, on 23 randomly drawn pages with checked expectations:** AgentCrawl kept 99 % of the checked text with 10.5 % noise offline, and 99 % with 8 % live. It is ahead of Scrapling on quality (p < 0.001). **Against Firecrawl and Crawl4AI the quality difference is not significant** (p = 0.13 and 0.07).
- **Tokens:** on random pages AgentCrawl's output is about a third smaller than Firecrawl's. Against Crawl4AI's `fit_markdown` it is 18 % larger offline and about the same live. On the 12 chosen pages it is about a third smaller than `fit_markdown`.
- **Combined score** (pages read × quality of what came back): AgentCrawl 58, Firecrawl 53, Crawl4AI 45, Scrapling 6. This is our own formula, and the gap between AgentCrawl and Firecrawl is inside the noise of both inputs.

## What was measured

Three lanes, each answering a different question:

| Lane | Question | Pages | Tools |
|---|---|---|---|
| **Web coverage** | Does the tool get readable content from a real site at all? | 400 real sites, 2 runs × 2 time caps | all four |
| **Offline quality** | Given the same HTML, how much of the right text comes back, and how much noise? | 35 saved pages (12 chosen + 23 drawn at random) | local tools |
| **Live quality** | The same question for Firecrawl, a hosted API that cannot read our saved pages | the same 35 URLs, fetched live by every tool in one run | all four |

**Web coverage.** The sample is 400 sites drawn at random (seed 2026, on 2026-09-27) from the [Tranco](https://tranco-list.eu/) top 100,000: 160 from ranks 1–1,000, 160 from 1,001–10,000 and 80 from 10,001–100,000. It has 310 homepages and 90 inner pages, all inner pages from ranks 1–10,000, picked at random from the site's sitemap (65) or, when it has none, from Common Crawl (25, index `CC-MAIN-2026-39`). The sample is [`web-sample.json`](../benchmarks/results/0.6.0/web-sample.json).

Each tool gets the URL and fetches it the way a user of that tool would. A result counts as **content** when it has at least 300 readable characters and is not a challenge page, an error page (HTTP ≥ 400) or an error body. This checks that something readable came back, not that it is the right text; that is what the quality lanes are for.

Time caps are 60 s or 180 s per page:

- Local tools get the cap as their own timeout, and a page that takes longer than the cap plus 15 s counts as failed.
- Firecrawl gets the cap as its API `timeout`. It is not cut at grading, because its time includes our own waits on its rate limit.

**Quality (offline and live).** Each of the 35 pages has *expected* signals: sentences, plus a few table or code markers, that must appear. Most also have *excluded* signals that must not appear, such as navigation, cookie banners, footers and related links. We report:

- **text recall**: share of expected signals found;
- **noise**: share of excluded strings found;
- **clean**: pages with every expected signal and no excluded one;
- **tokens**: total output size with OpenAI's `o200k_base` tokenizer (tiktoken 0.14.0).

The 12 chosen pages were picked by this project: documentation and reference pages, a Wikipedia article, a blog post and a scraping-sandbox product page.

The 23 drawn pages come from a second Tranco sample: seed 20261015, 142 pages, drawn on 2026-10-15, including a block of news, product, forum and government pages ([`draw-sample.json`](../benchmarks/results/0.6.0/draw-sample.json)). `benchmarks/draw_neutral.py` kept pages in draw order when they passed mechanical filters only:

- an inner or hard-block page;
- HTTP 200 with no challenge to a plain HTTP fetch, no browser (AgentCrawl's challenge detector run on the raw HTML), so pages that need JavaScript are not in the quality set;
- allowed by robots.txt;
- at least 1,500 characters of visible text;
- at most 3 pages per domain and 6 per page kind.

The drawn pages' signals were written by a separate AI agent from the saved HTML only (no tool output), and committed before any tool ran on them. Three drawn pages had no main content to check and were left unscored.

## Results

### Web coverage

Pages with readable content, both runs pooled (pages tried in brackets):

| tool | 60 s cap | 180 s cap |
|---|---|---|
| Scrapling | 64.5 % (800) | 66.0 % (800) |
| Firecrawl | 65.0 % (688) | 64.8 % (565) |
| AgentCrawl | 63.6 % (800) | 64.8 % (800) |
| Crawl4AI | 55.3 % (800) | 55.8 % (800) |

AgentCrawl against each tool, counting the (page, run) pairs where only one of the two got content, with an exact McNemar test:

| pair | 60 s: only AgentCrawl / only other | p | 180 s: only AgentCrawl / only other | p | verdict |
|---|---|---|---|---|---|
| vs Crawl4AI | 84 / 17 | < 0.001 | 86 / 14 | < 0.001 | AgentCrawl ahead |
| vs Firecrawl | 45 / 59 | 0.20 | 30 / 40 | 0.28 | no significant difference |
| vs Scrapling | 38 / 45 | 0.51 | 25 / 35 | 0.25 | no significant difference |

Firecrawl and Scrapling also show no significant difference from each other (p = 0.67 and 1.0). Raising the cap from 60 s to 180 s changes any tool's coverage by 1.5 points or less.

Part of the gap with Crawl4AI is its main-content filter rather than fetching. Some pages came back from Crawl4AI with raw Markdown but under 300 characters after `fit_markdown`'s filter, so they count as not read. The per-page data behind the count went with the deleted runs; the next release's runs will give it again.

### Offline quality (35 pages, same HTML for every tool)

| tool | text recall | noise | clean pages | tokens |
|---|---|---|---|---|
| AgentCrawl | 99.4 % | 9.8 % | 28/35 | 277,995 |
| Crawl4AI `fit_markdown` | 94.3 % | 12.4 % | 22/35 | 395,107 |
| trafilatura | 84.3 % | 0 % | 24/35 | 144,583 |
| Scrapling | 97.1 % | 88.8 % | 2/35 | 463,890 |
| Crawl4AI `raw_markdown` | 97.1 % | 90.5 % | 2/35 | 5,370,972 |

The 23 drawn pages alone:

| tool | text recall | noise | clean pages | tokens |
|---|---|---|---|---|
| AgentCrawl | 99.1 % | 10.5 % | 17/23 | 39,815 |
| Crawl4AI `fit_markdown` | 91.3 % | 14.9 % | 13/23 | 33,663 |
| trafilatura | 80.4 % | 0 % | 14/23 | 15,371 |

On the drawn pages AgentCrawl uses 18 % more tokens than `fit_markdown`. The token gap over the whole set comes from the 12 chosen pages, where AgentCrawl was also tuned (see below), and there `fit_markdown` leaks slightly less (7.6 % vs 8.3 %). trafilatura is the most compact and leaks nothing, but loses about a sixth of the checked text (a fifth on the drawn pages) and every code-language tag.

### Live quality (the same 35 URLs, fetched live)

The 23 drawn pages:

| tool | pages returned | text recall | noise | clean pages | tokens | quality |
|---|---|---|---|---|---|---|
| AgentCrawl | 23/23 | 99.1 % | 8.3 % | 18 | 39,393 | 0.91 |
| Firecrawl | 22/23 | 96.4 % | 14.0 % | 13 | 61,352 | 0.82 |
| Crawl4AI | 22/23 | 97.3 % | 17.0 % | 12 | 37,256 | 0.80 |
| Scrapling | 23/23 | 97.8 % | 91.3 % | 0 | 148,924 | 0.09 |

*Quality* is the mean of recall × (1 − noise) over the pages a tool returned. Recall, noise and tokens are over those pages too. On the 22 pages both returned, AgentCrawl's output is 2 % smaller than Crawl4AI's and 37 % smaller than Firecrawl's.

Per page, AgentCrawl against each tool. The table shows pages where AgentCrawl's recall × (1 − noise) was higher / lower than the other tool's, with an exact sign test. Here a page a tool did not return counts as 0.

| pair | 23 drawn pages | p | all 35 pages | p |
|---|---|---|---|---|
| vs Firecrawl | 6 / 1 | 0.13 | 8 / 4 | 0.39 |
| vs Crawl4AI | 7 / 1 | 0.07 | 10 / 4 | 0.18 |
| vs Scrapling | 23 / 0 | < 0.001 | 32 / 1 | < 0.001 |

One of the 6 pages counted for AgentCrawl against Firecrawl, and one of the 7 against Crawl4AI, is a page the other tool did not return.

### Combined score

`coverage × quality` is the share of pages read (web lane, both caps pooled) times the live quality on the 23 drawn pages. It is a page-read rate weighted by how much of the checked text came back without chrome.

| tool | coverage | quality | score |
|---|---|---|---|
| AgentCrawl | 64.2 % | 0.91 | 58 |
| Firecrawl | 64.9 % | 0.82 | 53 |
| Crawl4AI | 55.5 % | 0.80 | 45 |
| Scrapling | 65.2 % | 0.09 | 6 |

## How to read this

- **Datacenter IP.** The local tools ran on a GitHub Actions runner, whose IP many sites block. Firecrawl fetches from its own infrastructure.
- **Unreadable pages.** In each run, 26–29 % of pages were read by no tool, Firecrawl included: sites that were down, blocked, or had no readable text. That caps every coverage number.
- **Local tools share one runner.** AgentCrawl, Crawl4AI and Scrapling start at the same time on one runner, two pages at a time each, so IP-based anti-bot decisions hit them alike.
- **Settings.** AgentCrawl ran with its optional stealth extra (Patchright retry). Scrapling ran with its strongest local fetcher (`StealthyFetcher` with the Cloudflare click). Crawl4AI ran with its default browser settings, not its stealth options.
- **Firecrawl's second runs stopped early.** The account ran out of credits during the second run of each cap: after page 288 at 60 s, and after page 165 at 180 s.
  - Pages refused for lack of credits (HTTP 402) are left out, not counted as failures, and the pairwise tests only compare pages both tools were tried on.
  - Firecrawl's coverage therefore rests on 1,253 pages instead of 1,600.
  - The sample is ordered by rank, so those second-run pages are almost all top-1,000 sites (180 s) or top-10,000 sites (60 s). Firecrawl's pooled coverage over-weights popular sites.
- **The signals were written by this project.** The 23 drawn pages were picked mechanically and their signals written before any tool ran on them, but by us. AgentCrawl's extractor has been fixed against the 12 chosen pages in the past, so its score there is optimistic. That is why the drawn pages are reported on their own. Between the draw of the 400-site sample and these runs, AgentCrawl's engine was also fixed against pages of that same sample that other tools read and it did not, so its web coverage here may be optimistic; the fresh sample at the top of this page found no evidence of a change.
- **Live ran about two hours after the pages were saved** (22:22 UTC on 15 October vs 00:08 UTC on 16 October). A page that changed in between lowers every tool's recall.
- **Small samples.** 23 drawn pages can separate large differences (vs Scrapling) but not small ones (vs Firecrawl, vs Crawl4AI).
- **What "content" lets through.** The 300-character threshold also counts navigation-only output as content, which helps tools that return the whole page.
- **Scrapling** is measured with the Markdown its MCP server returns. That conversion removes scripts and hidden text but does not select the main content, so its noise figure measures the conversion, not its fetching, where it shows no significant difference from the best.
- **Crawl4AI** is measured as `fit_markdown` (its main-content filter) in the web and live lanes; offline, both variants are shown.
- **The combined score is our formula.** It treats a missed page and a page full of chrome alike. If you weigh noise less, Scrapling moves up. All the inputs are in the tables above.
- **Not measured here:** crawling, search, speed at scale, structured extraction, pages behind logins, and protected sites from a residential IP.

## Versions and runs

| | |
|---|---|
| AgentCrawl | source at commit `630f833` (web) and `812d60e` (live), reported as 0.5.6 before the 0.6.0 version bump; offline at `c274701` |
| Crawl4AI | 0.9.5 |
| Scrapling | 0.4.15 |
| Playwright / Patchright | 1.64.0 / 1.64.1 |
| trafilatura / html2text | 2.3.1 / 2025.4.15 (offline only) |
| Firecrawl | hosted API v2 `/scrape`, `onlyMainContent: true` |
| Tokenizer | tiktoken 0.14.0, `o200k_base` |

Workflow runs ([web-benchmark.yml](../.github/workflows/web-benchmark.yml)). The sample run is from 2026-09-29; the rest ran on 2026-10-15/16 UTC.

| lane | runs |
|---|---|
| sample | [36514612561](https://github.com/JorG18/agentcrawl/actions/runs/36514612561) |
| web, 60 s cap | 38814822376, 38824289158 (deleted; summary in [`web-60s.json`](../benchmarks/results/0.6.0/web-60s.json)) |
| web, 180 s cap | 38814835609, 38825325668 (deleted; summary in [`web-180s.json`](../benchmarks/results/0.6.0/web-180s.json)) |
| live quality | [38827740605](https://github.com/JorG18/agentcrawl/actions/runs/38827740605) |

The four web runs were deleted on 2026-10-18 because they also carried results of a hosted tool whose terms do not allow publishing them; their per-page data went with them, and the committed summaries are what remains. The next release's runs will be public again. The live run shows as failed on GitHub because its summary job crashed. The scraping jobs succeeded, and their artifacts are the ones used here.

The offline lane ran locally (Linux, Python 3.14.4) on pages saved on 2026-10-15. Their SHA-256 hashes are in [`offline-quality-35.json`](../benchmarks/results/0.6.0/offline-quality-35.json) and [`benchmarks/corpus/neutral.json`](../benchmarks/corpus/neutral.json).

## Reproduce

GitHub keeps run artifacts for 14 days. The summaries and both samples in [`benchmarks/results/0.6.0/`](../benchmarks/results/0.6.0/) stay.

Web coverage cannot be re-pooled from the 0.6.0 runs (deleted, see above): run the workflow again (below) and pool your runs with `python -m benchmarks.web.aggregate --run runs/<id> --run runs/<id>`.

```bash
# Live quality
gh run download 38827740605 -n web-local -n web-firecrawl -D runs/live
python -m benchmarks.web.live_quality --run runs/live --prefix drawn

# Offline quality (saves the pages first; needs crawl4ai, trafilatura, scrapling, tiktoken)
python -m benchmarks.snapshot
python -m benchmarks.compare --corpus benchmarks/corpus/neutral.json \
  --tools agentcrawl,crawl4ai,crawl4ai-fit,trafilatura,scrapling
```

The offline numbers can only be re-scored on the original page bytes. A fresh `snapshot` saves today's pages, and a page whose bytes changed is listed as `snapshot changed` and left unscored.

The fresh-sample runs are public: [40128584435](https://github.com/JorG18/agentcrawl/actions/runs/40128584435) and [40130001503](https://github.com/JorG18/agentcrawl/actions/runs/40130001503) (fresh sample), [40128594303](https://github.com/JorG18/agentcrawl/actions/runs/40128594303) (0.6.0 sample, same day). Their per-run summaries were made with `python -m benchmarks.web.aggregate --run runs/<id> --out <file>`, one run at a time. The pooled `web-60s.json` also carries a p for the two runs together (0.03 against Scrapling); it is descriptive only, since the runs repeat the same pages, and the verdict uses the per-run tests.

To run it yourself, dispatch the **Web benchmark** workflow with `stealth=1`, a `page_timeout` of `60` or `180`, and either of:

- `sample_path=benchmarks/results/0.6.0/web-sample.json` or `benchmarks/results/2026-10-fresh/web-sample.json`: the same 400 sites;
- `sample_path=benchmarks/corpus/live_quality.json`: the 35 quality pages.

Firecrawl needs a `FIRECRAWL_API_KEY` repository secret and spends its credits.
