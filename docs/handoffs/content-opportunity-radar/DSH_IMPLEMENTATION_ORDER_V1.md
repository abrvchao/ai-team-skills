# DSH Implementation Order — Content Opportunity Radar V1

## Purpose

This document is the implementation contract for DSH.

DSH must **not redefine the product**. Implement the architecture below, then submit code, docs, tests, and a PR/diff for review.

Reviewer: ChatGPT
Implementation Engineer: DSH

## Product architecture

```
Sources
  ↓
Connectors / Providers
  ↓
Raw Events
  ↓
Snapshots
  ↓
Normalization
  ↓
Unified Signals
  ↓
Topic / Entity Resolution
  ↓
Time-series Features
  ↓
Opportunity Engine
  ↓
AI Explanation / Research
```

Principle:

- Data/statistics layer determines **What is happening**.
- LLM explains **Why it matters**.
- LLM must not directly invent or decide whether a trend objectively exists.

## Phase 1 scope

Do not build a polished dashboard yet.

Implement:

1. Provider Core
2. Unified schemas
3. Website Provider
4. GitHub Provider
5. Hacker News Provider
6. Google News Provider
7. GDELT Provider
8. Snapshot Engine
9. Feature Engine V1
10. Opportunity Score V1
11. Tests
12. Real end-to-end demo for topic `AI Agents`

---

## 1. Provider Core

Implement:

- `DataProvider`
- `ProviderRegistry`
- `CollectionRequest`
- `CollectionResult`
- `ProviderHealth`
- Rate Limit abstraction
- Retry / Backoff
- Cache / stale handling
- provenance
- acquisition method

Provider health states:

```
healthy
degraded
stale
rate_limited
auth_required
disabled
failed
```

Every provider must define:

```
primary
fallback
cache
retry
rate limit
staleness TTL
health state
terms class
```

Failure in one provider must never crash the whole Radar.

---

## 2. Unified schemas

### RawEvent

Suggested shape:

```ts
interface RawEvent {
  id: string
  provider: string
  source: string
  acquisitionMethod:
    | "official_api"
    | "oauth_api"
    | "rss"
    | "atom"
    | "public_web_api"
    | "html"
    | "search_provider"
    | "licensed"

  externalId?: string
  url?: string
  title?: string
  text?: string
  author?: string
  community?: string
  publishedAt?: Date
  retrievedAt: Date
  language?: string
  country?: string
  metrics?: Record<string, number>
  raw: unknown

  provenance: {
    termsClass: string
    apiVersion?: string
    endpoint?: string
  }
}
```

### Signal

```ts
interface Signal {
  id: string
  topicId: string
  entityIds: string[]

  signalType:
    | "demand"
    | "momentum"
    | "supply"
    | "authority"
    | "commercial"
    | "pain"
    | "question"
    | "research"
    | "media"

  source: string
  provider: string
  observedAt: Date
  value: number
  normalizedValue: number

  baseline?: number
  delta?: number
  velocity?: number
  acceleration?: number

  confidence: number
  geo?: string
  language?: string
  evidenceIds: string[]
  freshnessSeconds: number
}
```

### MetricSnapshot

```ts
interface MetricSnapshot {
  subjectType:
    | "topic"
    | "repo"
    | "video"
    | "query"
    | "article"
    | "domain"

  subjectId: string
  metric: string
  value: number
  collectedAt: Date
  provider: string
}
```

Traceability requirement:

```
Signal
→ Raw Event
→ Provider
→ Source URL / API Endpoint
```

---

## 3. Website Provider

Implement source discovery in this order:

```
RSS / Atom
↓ fail
Sitemap
↓ fail
robots.txt → sitemap
↓ fail
HTML discovery
↓
Page Watch fallback
```

Probe common paths:

```
/feed
/feed.xml
/rss
/rss.xml
/atom.xml
/blog/feed
/news/rss
/changelog/rss
/sitemap.xml
/sitemap_index.xml
robots.txt
```

Store at least:

```
url
canonical_url
title
description
h1
published_at
modified_at
content_hash
topic_ids
first_seen_at
last_seen_at
```

Signals:

```
competitor_new_content_count
competitor_topic_coverage
topic_supply_growth
content_freshness
content_gap
```

Use for both user websites and competitor websites.

---

## 4. GitHub Provider

Use the official GitHub API.

### Repository momentum

Collect:

```
repo
topic
stars
forks
open_issues
created_at
pushed_at
language
```

Snapshot changing metrics. Do not only store latest state.

Calculate:

```
stars_velocity_1d
stars_velocity_7d
star_acceleration
fork_velocity
new_repo_count
```

### Issues demand / pain

Collect:

```
title
body
comments
reactions
repo
created_at
```

Classify evidence such as:

```
question
pain
feature_request
integration_request
hosted_request
commercial_intent
```

LLM may assist classification, but it must not generate the trend score itself.

If rate limited:

```
use recent snapshot
mark signal_stale
retry on next scheduled collection
```

Do not fall back to scraping GitHub search HTML.

---

## 5. Hacker News Provider

Primary:

```
HN Algolia Search API
```

Fallback:

```
HN RSS
```

Queries should come from tracked topics/entities, not only hard-coded AI keywords.

Collect:

```
story_id
title
points
comments
author
created_at
url
```

Produce:

```
story_count
points_sum
comments_sum
discussion_velocity
```

---

## 6. Google News Provider

Prefer RSS Search.

Produce:

```
news_mentions
publisher_count
fresh_article_count
media_velocity
```

---

## 7. GDELT Provider

Use GDELT DOC API.

Collect:

```
article
publisher
country
language
published_at
```

Produce:

```
media_velocity
publisher_diversity
geo_spread
cross_country_spread
```

Google News and GDELT must be deduplicated where possible. Do not simply add raw counts together.

---

## 8. Snapshot Engine

This is a Phase 1 core requirement.

All changing metrics must preserve historical snapshots.

At minimum:

```
repo stars
repo forks
HN mentions
HN comments
news mentions
competitor content count
```

Forbidden pattern:

```
latest_value_only
```

Historical time series must support later velocity and acceleration calculations.

---

## 9. Feature Engine V1

No ML model required in V1.

Implement:

```
delta
velocity
acceleration
moving_average
z_score
persistence
source_diversity
cross_source_confirmation
```

Especially implement:

```
CrossSourceConfirmation(topic)
```

Example:

```
GitHub ↑
HN ↑
Google News ↑
GDELT ↑
```

must produce stronger confidence than a single-source spike.

---

## 10. Opportunity Score V1

Use deterministic calculations.

Initial weights:

```
22% Demand
20% Momentum
18% Supply Gap
15% Authority Fit
15% Business Fit
5% Freshness
5% Confidence
```

Missing signals may reduce confidence.

Forbidden:

```
Prompt: "Does this topic feel hot? Give it a score."
```

Every score component must expose evidence.

---

## 11. Topic / Entity layer

Support at least these entity classes:

```
Topic
Entity
Company
Product
Technology
Person
Repository
Domain
Keyword
```

Relationships may include:

```
RELATED_TO
SUBTOPIC_OF
MENTIONS
COMPETES_WITH
PUBLISHED_BY
RANKS_FOR
USES
```

Different providers referring to the same real topic should normalize toward the same topic node.

---

## 12. Repository structure

Suggested layout:

```
content-opportunity-radar/

apps/
  web/
  api/

packages/
  providers/
    core/
    website/
    github/
    hackernews/
    googlenews/
    gdelt/

  signals/
    schema/
    normalization/
    snapshots/
    features/

  topic-graph/
  opportunity-engine/

  ai/
    explanation/
    research/

docs/
  DATA_SOURCE_ARCHITECTURE_V1.md
  DATA_SOURCE_MATRIX.md
  PROVIDER_CONTRACT.md
  SIGNAL_SCHEMA.md
  ADR/
```

---

## 13. Tests

Must include:

- provider unit tests
- normalization tests
- snapshot tests
- velocity / acceleration tests
- cross-source confirmation tests
- failure isolation tests
- rate-limit tests
- fixture tests

Fixtures are allowed for unit/integration tests, but the final demo must include real internet data.

---

## 14. Real demo

Topic:

```
AI Agents
```

Must demonstrate:

```
Internet
↓
GitHub / HN / Google News / GDELT
↓
RawEvent
↓
Snapshot
↓
Normalized Signal
↓
Topic
↓
Features
↓
Opportunity
↓
Evidence
```

Do not use all-mock data to claim success.

---

## 15. Definition of Done

Any generated opportunity must be able to answer:

1. Why did this opportunity appear?
2. Which sources support it?
3. Which metric is increasing?
4. When did it start increasing?
5. What is the content supply situation?
6. How fresh is the data?
7. What is the confidence?
8. Where is the evidence?

Phase 1 is complete only when this real-source path works:

```
Real Sources
↓
Provider
↓
RawEvent
↓
Snapshot
↓
Signal
↓
Feature
↓
Opportunity Score
↓
Evidence
```

When Phase 1 is complete, stop expanding data-source coverage and submit code, docs, tests, and PR/diff for review.

Do not start GSC, Keyword Planner, YouTube, Reddit, Product Hunt, Hugging Face, arXiv, Google Trends, or China social sources until Reviewer approves Phase 1.
