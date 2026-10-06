# Daily News Digest – Project Plan

Last updated: 2026-10-06

## Goal and scope

Every morning at a fixed Vienna time, a cloud job builds a curated digest of serious news and new AI papers, publishes it as an HTML page, and sends a WhatsApp message with the link.

**Topic groups**

- **Tech:** AI, informatics, software engineering (tools, languages, security, industry moves).
- **Geopolitics:** important world events, plus new laws and regulations that get little coverage, with focus on Austria and the EU.
- **Science:** new AI papers, models, benchmarks and techniques.

**Hard constraints**

- Runs in the cloud on a schedule; no local machine needed.
- Free infrastructure; only LLM usage may cost a few cents a day.
- Sources come from a fixed allowlist of reputable outlets and primary sources. The LLM ranks and summarizes, it never decides what counts as trustworthy.
- Summaries are written in each article's original language (German or English, mostly).
- Delivery: one WhatsApp message to my own number with the page link.

**Out of scope for v1:** user accounts, a mobile app, interactive feedback, paywalled full-text scraping.

## Architecture

One scheduled GitHub Actions job runs the whole pipeline in Python; GitHub Pages hosts the result and CallMeBot delivers the link to WhatsApp.

```
sources.yaml -> [GitHub Actions daily run]
  Fetch -> Pre-filter -> Cluster/Dedupe -> Enrich -> Rank (Haiku) -> Summarize -> Render HTML
  -> GitHub Pages (page + archive)
  -> CallMeBot WhatsApp message with link -> my phone
```

Only the Rank and Summarize steps call the Anthropic API; everything else is free, deterministic code that can be tested offline.

## Topics and source allowlist

Sources are an explicit allowlist in `config/sources.yaml`; anything not on it never enters the pipeline. Primary sources (parliaments, official journals, research labs) are preferred because they cover the overlooked laws and new models first-hand. Claude Code must verify every feed URL at implementation time and drop or replace dead ones.

| Group | Tier | Sources | Access |
| --- | --- | --- | --- |
| Austria laws & politics | Primary | Parlamentskorrespondenz (parlament.gv.at), RIS – Bundesgesetzblatt | RSS |
| Austria news | Journalism | ORF.at, Der Standard, Die Presse, profil | RSS |
| EU laws & policy | Primary | EUR-Lex Official Journal (L series), European Parliament press, Council of the EU press | RSS |
| EU news | Journalism | Euractiv, Politico Europe (free articles only) | RSS |
| World news | Journalism | AP News, BBC World, The Guardian (Open Platform API), Deutsche Welle, NZZ, Le Monde (English) | RSS / API |
| Geopolitical analysis | Think tanks | SWP Berlin, ECFR, Chatham House, Carnegie Endowment, Foreign Affairs (free articles) | RSS |
| Tech & SWE | Journalism | Heise, Golem, Ars Technica, The Register, LWN.net, IEEE Spectrum, MIT Technology Review, InfoQ | RSS |
| AI papers | Primary | arXiv (cs.AI, cs.LG, cs.CL, cs.SE), Hugging Face Daily Papers | API |
| AI labs & models | Primary | Anthropic, OpenAI, Google DeepMind, Meta AI, Mistral blogs/news | RSS or page diff |
| Science journalism | Journalism | Quanta Magazine, Nature News | RSS |

**Rules**

- Each source carries `group`, `tier`, `language` and `weight` (1–3) in config; weight feeds into ranking.
- No aggregators, tabloids or social media. Reuters is excluded only because it has no public RSS; add it later if an official feed exists.
- Paywalled outlets are fine as long as the RSS teaser is enough to summarize; never bypass paywalls.

## Pipeline

The daily run is seven steps, each a separate module that can be run and tested alone with `--dry-run`.

1. **Fetch** all allowlisted feeds and APIs from the last 24–36 h in parallel (httpx + feedparser, arXiv API). Normalize to an `Item` model: id, url, title, source, group, tier, language, published\_at, teaser.
2. **Pre-filter in code, no LLM.** Rules in `config/filters.yaml` drop routine items from primary law feeds (corrections, tariff tables, purely administrative ordinances) by document type and keywords, plus items with empty teasers. This keeps token cost low and stops important laws from drowning in noise.
3. **Deduplicate and cluster.** Canonical URL hash first, then title similarity (rapidfuzz, threshold \~90) groups the same story from several outlets into one `Cluster`. The lead item is picked by source tier and weight; the others become "also covered by" links. Clusters already sent are dropped via `state/seen.json`.
4. **Enrich** where allowed: extract the lead item's text with trafilatura (respect robots.txt, timeout, cap \~2,000 words). Papers use the arXiv abstract. If extraction fails, keep the teaser.
5. **Filter and rank** in one cheap LLM pass (Claude Haiku) per group: score each cluster 0–10 for relevance to my topics and real-world importance, with a one-line reason. Final score = LLM score × source weight + recency bonus + coverage bonus (more reputable outlets = more important). Keep top N per group (default 8; papers 5; laws at least 2). Every scored cluster, kept or dropped, is written to `out/scored.json`.
6. **Summarize** each kept cluster: 2–4 sentences in the lead article's original language, plus one line "why it matters". Laws get extra fields: jurisdiction, status (proposed / passed / in force), effective date if stated. Use tool-use / JSON output, validated with Pydantic; retry once on invalid JSON.
7. **Daily overview in German:** one short paragraph per group connecting the top stories. It is the only text not in the source language, because it spans sources in several languages.

**Guardrails**

- The LLM sees only fetched text, never searches the web, so every summary is grounded in a linked source.
- Prompt explicitly: no speculation beyond the text; say "unclear from source" when needed.
- Hard cap on tokens per run; abort summarization gracefully and still publish headlines if the cap is hit.

## Output, hosting and delivery

**HTML page**

- Static page rendered with Jinja2: one file per day at `/digest/YYYY-MM-DD.html`, plus `index.html` pointing to the latest and an archive list.
- Layout: date header, three sections (Tech, Geopolitics, Science), each with the German overview paragraph and item cards (title as link, source, language tag, summary, "why it matters", "also covered by" links). Laws show a jurisdiction and status badge.
- Collapsed "Also considered" section at the bottom: dropped clusters with score and one-line reason, so ranking can be audited and tuned.
- Mobile first, no external JS, light/dark via `prefers-color-scheme`, under 200 KB.

**Hosting:** GitHub Pages from the `gh-pages` branch. Free Pages requires a public repo, so the page is public but not indexed (`noindex` meta, no sitemap). If privacy matters later, switch to Cloudflare Pages with Access.

**WhatsApp:** CallMeBot WhatsApp API, sending to my own number. One-time setup: message the CallMeBot number to get an API key. Message format: `📰 Digest 06.10.2026 — 8 Tech · 6 Geo · 5 Papers` + link. On pipeline failure, send a short error message instead so silence never means "no news". Telegram bot is the documented fallback channel behind the same `Notifier` interface.

**Scheduling:** GitHub Actions cron runs in UTC and ignores daylight saving. Schedule two crons (05:00 and 06:00 UTC) and let the script exit early unless the Vienna local hour equals `DELIVERY_HOUR` (default 07:00). Expect GitHub cron to start up to \~15 min late. Add `workflow_dispatch` for manual runs. Note: GitHub disables schedules in repos without activity for 60 days; the daily state commit prevents this.

## Repo structure, config and secrets

```
news-digest/
├── .github/workflows/daily.yml     # cron + manual trigger, deploy to gh-pages
├── config/
│   ├── sources.yaml                # allowlist: url, type, group, tier, language, weight
│   ├── filters.yaml                # pre-filter rules for routine law items
│   ├── topics.yaml                 # topic descriptions + keywords used in ranking prompt
│   └── settings.yaml               # delivery hour, top-N per group, models, token cap
├── src/digest/
│   ├── models.py                   # Pydantic: Item, Cluster, ScoredCluster, Summary, Digest
│   ├── fetch/                      # rss.py, arxiv.py, guardian.py, pages.py (no-RSS sources)
│   ├── prefilter.py
│   ├── cluster.py                  # dedupe + group same story across outlets
│   ├── enrich.py                   # trafilatura extraction
│   ├── rank.py                     # LLM scoring + weighting, writes out/scored.json
│   ├── summarize.py                # LLM summaries + German overview
│   ├── llm.py                      # Anthropic client, retries, token budget, cost log
│   ├── render.py                   # Jinja2 → site/
│   ├── notify/                     # Notifier base, callmebot.py, telegram.py
│   └── main.py                     # CLI: run, --dry-run, --stage, --date, --from-fixture
├── scripts/
│   ├── check_feeds.py              # validates every source, lists dead ones
│   └── record_fixtures.py          # saves a real run's raw items for offline tuning
├── templates/                      # digest.html.j2, index.html.j2, styles.css
├── state/seen.json                 # sent cluster hashes, pruned after 30 days
├── out/                            # gitignored: digest.json, scored.json
├── tests/                          # unit tests, LLM mocked; fixtures/runs/ holds recorded runs
├── CLAUDE.md                       # this plan for Claude Code
└── pyproject.toml                  # uv, ruff, pytest, mypy
```

**Stack:** Python 3.12, uv, httpx, feedparser, trafilatura, rapidfuzz, pydantic, jinja2, anthropic SDK, tenacity, pytest.

**GitHub Actions secrets**

| Secret | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Ranking and summaries (Anthropic Console, separate from a Claude.ai subscription) |
| `CALLMEBOT_PHONE` | My number in international format |
| `CALLMEBOT_APIKEY` | Key from CallMeBot setup |
| `GUARDIAN_API_KEY` | Free Guardian Open Platform key (optional) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Fallback notifier (optional) |

Model IDs live in `settings.yaml`, not code: Haiku for ranking, Haiku or Sonnet for summaries.

## Milestones for Claude Code

Give Claude Code one milestone per session, starting with: "Read CLAUDE.md, implement milestone N, run the tests, stop for review." Each milestone ends with green tests and a working `--dry-run`.

1. **M0 – Skeleton.** uv project, folder layout, Pydantic models, settings loader, CLI stub, ruff + pytest + mypy in CI.
   - Done when: `uv run digest --dry-run` prints loaded config; CI passes.
2. **M0.5 – Risk spike (do before anything else).** Send one WhatsApp message via CallMeBot from a GitHub Actions run. Write `scripts/check_feeds.py` and check every source in `sources.yaml`; mark each as RSS, API, needs page scraper, or dropped.
   - Done when: a WhatsApp message arrived from Actions, and `sources.yaml` contains only verified sources with their access type.
3. **M1 – Fetching and pre-filter.** RSS, arXiv, Guardian and page fetchers, async with timeouts; one failing source never kills the run. `prefilter.py` with rules from `filters.yaml`.
   - Done when: dry run prints item counts per source before and after pre-filtering.
4. **M2 – Clustering and state.** URL hashing, fuzzy title clustering with lead selection, `seen.json` read/write with 30-day pruning.
   - Done when: the same story from 3 outlets becomes one cluster in tests; running twice on the same fixtures yields zero new clusters the second time.
5. **M3 – Enrich.** trafilatura extraction with fallback to teaser.
   - Done when: tests cover success, paywall stub and timeout.
6. **M4 – LLM ranking and summaries.** `llm.py` with retries, token budget and cost log; ranking with reasons, `out/scored.json`, summaries, German overview, JSON validation. Run `record_fixtures.py` on 3–5 real days.
   - Done when: `--from-fixture` reproduces a full digest offline; token usage and cost are logged; tests mock the API.
7. **M5 – Rendering.** Jinja2 templates, daily page with "also covered by" and "Also considered", index, archive.
   - Done when: the page looks right on a phone-width browser and passes an HTML validator.
8. **M6 – Deploy.** GitHub Actions workflow: DST-safe double cron, run, commit `state/`, publish `site/` to `gh-pages`.
   - Done when: a manual `workflow_dispatch` run publishes a reachable page.
9. **M7 – WhatsApp.** `Notifier` interface, CallMeBot implementation, error notification, Telegram fallback.
   - Done when: a manual run delivers the WhatsApp message with a working link.
10. **M8 – Hardening.** Run log summary in the Actions job, prompt tuning against recorded fixtures and `scored.json`.
    - Done when: seven consecutive scheduled runs delivered on time.

## Costs, risks and open decisions

Expected running cost is only the Anthropic API, roughly a few cents per day with Haiku; GitHub Actions, Pages and CallMeBot are free for this volume. Check current API pricing before launch and set a monthly spend limit in the Anthropic Console.

| Risk | Mitigation |
| --- | --- |
| CallMeBot is a third-party free service and may be slow or go down | Error/timeout handling, Telegram fallback behind the same interface |
| Feeds change URLs or break | Feed health check in every run, dead sources listed in the run log |
| LLM hallucinates details | Summaries only from fetched text, "unclear from source" rule, link always shown |
| Important laws drown in general news | Primary-source feeds get higher weight; laws keep a minimum of 2 slots per day |
| Cost overrun | Token cap per run, Haiku by default, spend limit in Console |
| Public page | `noindex`, unguessable path optional, move to Cloudflare Access if needed |

**Open decisions**

- [x] Delivery time: 07:00 Vienna
- [x] Overview language: German; item summaries in the original language
- [ ] Haiku or Sonnet for summaries, decided by comparing both on recorded fixtures in M4
- [ ] Final number of items per group, tuned in M8
