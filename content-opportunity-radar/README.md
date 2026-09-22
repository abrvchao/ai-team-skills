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
