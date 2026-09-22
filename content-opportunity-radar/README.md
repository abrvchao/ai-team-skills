# Content Opportunity Radar — Phase 1

A minimal **Signal Intelligence Engine** for identifying content opportunities from real internet signals.

Phase 1 intentionally prioritizes data acquisition, historical snapshots, deterministic trend features, provenance, and failure isolation over UI.

## Architecture

```
Sources
  → Providers
  → RawEvent
  → Append-only Snapshots
  → Signals
  → Topic/Entity normalization
  → Time-series Features
  → Explainable Opportunity Score
```

**Rule:** the data/statistics layer decides *what is happening*. An LLM may later explain *why it matters*, but does not create the trend score.

## Phase-1 providers

| Provider | Primary acquisition | Fallback / degradation |
| --- | --- | --- |
| GitHub | Official REST API | stale snapshot / rate-limited state; never scrape search HTML |
| Hacker News | Algolia API | HN RSS |
| Google News | RSS Search | degrade independently |
| GDELT | DOC 2.0 API | degrade independently |
| Website | RSS/Atom | Sitemap → robots.txt sitemap → webpage watch |

A single provider failure must not fail the entire pipeline.

## Run

Requires Python 3.10+ and no third-party packages.

```bash
cd content-opportunity-radar
python -m unittest -v test_phase1.py
python -m py_compile core.py entities.py web.py features.py opportunity.py pipeline.py
python pipeline.py --topic "AI Agents" --limit 10
```

Optional GitHub authentication improves API rate limits:

```bash
export GITHUB_TOKEN=...
python pipeline.py --topic "AI Agents" --limit 10
```

Optional website/competitor supply signal:

```bash
python pipeline.py --topic "AI Agents" --website https://example.com
```

Snapshots are appended to `.radar/snapshots.jsonl` by default. Repeated scheduled runs accumulate the historical series used for velocity and acceleration.

The last successful provider payload is cached in `.radar/provider-cache.json`. If a provider later fails, is rate-limited, or is temporarily degraded with no events, the pipeline can emit the cached evidence as `stale` rather than failing the whole Radar. Cached events retain their original retrieval time, so freshness/confidence can decay instead of pretending the data is live.

## Output

The CLI emits JSON containing:

- provider health and warnings
- raw evidence events
- normalized signals
- snapshot count
- source diversity
- cross-source confirmation
- explainable Opportunity Score components
- evidence IDs that trace the score back to source events

## Important behavior

- `healthy`, `degraded`, `rate_limited`, `failed` and other provider states are explicit.
- GDELT network/TLS/API failures should appear as `degraded`; other providers still run.
- Historical momentum requires at least two snapshots separated by at least 5 minutes.
- Flat or negative history does **not** become positive trend evidence.
- Cross-source confirmation counts materially positive signals; source diversity separately counts providers with evidence.
- Missing Phase-2 dimensions (first-party authority, business fit, richer supply-gap data) use neutral values and reduce confidence rather than being invented.

## Phase-1 boundary

Do not add GSC, Keyword Planner, YouTube, Reddit, Product Hunt, Hugging Face, arXiv, Google Trends, or China social connectors until Phase 1 review is complete.

See:

`../docs/handoffs/content-opportunity-radar/DSH_IMPLEMENTATION_ORDER_V1.md`


## Phase 2A — Google Search Console

`gsc.py` adds first-party Search Console evidence using the official Search Analytics API.

Runtime requirements:

- verified Search Console property
- OAuth access token with `https://www.googleapis.com/auth/webmasters.readonly`
- token is injected at runtime and is never written to RawEvent/cache/provenance

The provider backfills a 35-day daily window and derives query-level:

- demand from impressions
- momentum from recent 7-day daily demand vs the prior 28-day baseline
- authority from current ranking position and existing ranking pages

Search Console documents that Search Analytics is subject to internal limits and does not guarantee every row. The provider therefore records `top_rows_only_not_exhaustive` provenance and uses confidence below 100 rather than treating the dataset as complete.


## GSC → Opportunity integration

When `--gsc-site` is supplied, the pipeline can add Search Console first-party context without changing the behavior of users who have not connected GSC.

```bash
export GSC_ACCESS_TOKEN=...
python pipeline.py \
  --topic "AI Agents" \
  --gsc-site "sc-domain:example.com"
```

The token is runtime-only and is not accepted as a CLI argument.

Search Console query rows are first summarized per query and then collapsed to **exactly three topic-level signals**:

- Demand
- Momentum
- Authority

This prevents a high-row-count source from receiving extra scoring weight. The Opportunity Engine also aggregates values within each provider before averaging across providers, so 100 GitHub rows do not count as 100 independent votes against 1 Hacker News or GSC signal.

For this CLI integration, the GSC request defaults to a lightweight query-contains filter based on the topic. Production collection should instead ingest a site-level GSC window on a schedule and map that shared dataset to many Topic Graph nodes; it should not make a full Search Console request separately for every dashboard topic.

CTR-gap and query/page-gap are intentionally deferred until the Site Content Graph can compare GSC evidence with hydrated page content.


## Site Content Graph V1

The optional content-hydration layer turns GSC ranking URLs into deterministic page evidence.

Enable it explicitly:

```bash
export GSC_ACCESS_TOKEN=...
python pipeline.py \
  --topic "AI Agents" \
  --gsc-site "sc-domain:example.com" \
  --hydrate-content \
  --content-max-pages 20
```

The crawler is intentionally bounded:

- it hydrates only already-known/ranking URLs, not the open web
- URLs are prioritized by observed GSC impressions
- `robots.txt` is checked before hydration
- crawl delay is respected when declared
- page budget is capped
- page snapshots are append-only in `.radar/page-snapshots.jsonl`
- semantic content hashes detect page changes

Each hydrated page records:

- canonical URL
- title / description
- H1 / H2
- bounded main text
- internal links
- published / modified time when exposed
- content hash
- first seen / last seen
- change state

GSC evidence is connected as:

```
query
  → ranking page
  → hydrated page content
  → deterministic relevance
  → topic-level Supply signal
  → Opportunity Supply Gap
```

The same evidence layer also reports:

- query/page gap
- multiple-page cannibalization candidates
- stale winning pages
- CTR opportunity relative to the site's own comparable-position baseline

Unknown/unhydrated pages are treated as **unknown**, not as a proven content gap. An LLM is not used to create these scores.

The `SiteCrawler` and page graph are generic and can be reused for competitor URLs discovered by the Phase-1 WebsiteProvider. Multi-competitor scheduling and competitor-level supply aggregation are intentionally a separate follow-up rather than expanding this module into a full SEO crawler.

## Opportunity Discovery Loop V1

`radar.py` turns the single-topic pipeline into an actual Radar:

```
broad real-source seed collection
  → candidate topic extraction
  → alias / near-duplicate clustering
  → exclusive seed-evidence assignment
  → deep scan of top candidates
  → overlap-aware ranking
  → Top Opportunities
```

Run:

```bash
python radar.py --discover --scope "AI" --top 5
```

Useful options:

```bash
export GITHUB_TOKEN=...
export GSC_ACCESS_TOKEN=...

python radar.py \
  --discover \
  --scope "AI" \
  --candidates 8 \
  --top 5 \
  --gsc-site "sc-domain:example.com" \
  --hydrate-content
```

Discovery does not ask an LLM to invent topics. Candidates come from observed source titles/text, GitHub repository topics, and optional Search Console queries. Synonymous/near-duplicate candidates are clustered before deep scanning, each seed event is assigned to at most one candidate during discovery, and final ranking applies an evidence-overlap penalty so several near-identical opportunities cannot occupy the top slots using the same evidence.

The base Opportunity Score is not modified by the overlap penalty. The Radar adds a separate `rank_score` used only to order the Top Opportunities list. Opportunity Score components and evidence provenance remain inspectable.

Radar-level snapshots are appended to the normal snapshot store for:

- opportunity score
- rank score
- discovery score
- current rank

This allows later analysis of opportunity-rank movement over time.
