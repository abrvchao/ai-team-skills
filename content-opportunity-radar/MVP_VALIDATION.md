# Content Opportunity Radar — MVP Validation Baseline

Baseline date: 2026-09-29

This document records product evidence, not just test-suite health. A green
unit-test suite is necessary but is not treated as proof that the Radar produces
useful opportunities for real users.

## Current decision

The V1 technical loop is operational with real public data:

```
real public collection
  -> durable event store
  -> candidate discovery
  -> deep scan
  -> deterministic opportunity/rank scoring
  -> Research Pack
  -> read model / Dashboard API
  -> WeChat research handoff
```

The next product risk is **relevance and user value**, not missing platform
infrastructure. Do not add more generic providers until candidate quality and a
real first-party/design-partner workflow are measured.

## Evidence from the real public-source run

Scope: `AI Agents`

Public collection:

| Provider | State | Events |
| --- | --- | ---: |
| GitHub | healthy | 59 |
| Hacker News | healthy | 30 |
| Google News | healthy | 30 |
| GDELT | degraded | 0 |

Persisted store after collection:

- 119 raw events
- 119 event observations
- 246 metric snapshots
- 4 collection runs / scheduled jobs

GDELT degradation did not abort the run. This validates provider failure
isolation in a real network execution.

## Relevance defect found by the first real run

Read-model run `273cf1a7e67db66fc249f628` produced:

1. `chatgpt`
2. `AI Agents`
3. `python`

The `python` opportunity mixed programming evidence with news about snakes.
The root cause was not an LLM hallucination. A single-token GitHub repository
topic became a high-weight explicit candidate; the subsequent deep scan searched
the ambiguous word and pulled the other meaning.

The discovery layer now applies deterministic guards:

- public title-derived evidence must be supported by the requested scope;
- generic-only phrases do not become candidates;
- single-token GitHub topics do not independently become public-source
  opportunities;
- first-party GSC queries remain allowed to seed a single-token candidate,
  because they represent observed user/site demand.

These guards do not modify Opportunity Score.

## Post-fix real Top-5 baseline

Read-model run `2c17767fdd5a7b1b47a503a6` used the same persisted public
evidence and completed 8/8 deep scans with zero scan errors.

| Rank | Topic | Opportunity | Rank score |
| ---: | --- | ---: | ---: |
| 1 | prompt engineering | 54.05 | 54.05 |
| 2 | AI Agents | 52.81 | 52.81 |
| 3 | generative ai | 53.60 | 51.89 |
| 4 | large language models | 51.27 | 51.27 |
| 5 | claude code | 52.35 | 50.73 |

The obvious `python` ambiguity is gone. This is still **not** a claim that
precision@5 is acceptable: the five positions have not yet been human-labeled,
and the discovery evidence for these candidates is currently GitHub-heavy.

Use `candidate_eval.py` to create and score the human relevance queue.

## Downstream actionability smoke

A real Radar Research Pack was converted through `wechat_handoff.py` without
model reinterpretation.

Observed contract output:

- 28 traceable sources
- 24 source-backed claims
- 5 Radar interpretations kept as insights
- 7 evidence gaps / open questions
- every claim references known source IDs
- `facts_require_citation_ids=true` remains present in downstream guardrails

This validates the contract boundary:

```
Radar evidence
  -> Research Pack V1
  -> WeChat research contract
```

Article quality and publishing are separate downstream validations.

## China social data status

| Platform | Current production decision | Evidence |
| --- | --- | --- |
| Zhihu | Conditional Go | Official API paths documented/reachable; valid developer credential still needed for successful data sample and quota verification |
| Weibo | Defer until commercial/licensed access | Production search/trend/hot-list capability is in commercial data API; public-web/cookie paths are not production dependencies |
| Douyin | Defer capability spike | Do not build browser automation fallback |
| Bilibili | Defer capability spike | Validate documented/authorized vs public-web paths before any provider implementation |
| Xiaohongshu | Defer | Resume only for an official/licensed/search-index path suitable for commercial use |

No China-social platform is allowed to enter the production provider registry
merely because a browser-scraping path exists.

## MVP gates

| Gate | Definition | Current |
| --- | --- | --- |
| Public-source E2E | Real collection -> persisted Top Opportunities -> Research Pack | PASS |
| Provider degradation | One public provider can degrade without aborting the run | PASS |
| Downstream research contract | Real Research Pack converts to source-backed WeChat research input | PASS |
| Candidate relevance | Human-labeled precision@5 >= 0.60 on a minimum 20 evaluated opportunities | PENDING |
| First-party value | At least one real site runs with its own GSC data and produces useful opportunities | PENDING |
| Time to first value | Assisted onboarding -> first Top-5 report within one working day | PENDING |
| Repeat value | At least one design partner acts on >=1 Radar opportunity in each of two consecutive weeks | PENDING |
| China social | At least one authorized China source emits real SocialObservation data with recorded limits/provenance | PENDING |

## When to recruit first users

Do not wait for every China social platform or SaaS infrastructure feature.
Start an assisted design-partner pilot when:

1. candidate evaluation has at least 20 human-labeled opportunities and
   precision@5 is at least 0.60;
2. one real site has completed the GSC-backed workspace flow;
3. a Research Pack can be converted into the user's chosen content workflow
   without losing traceability;
4. provider degradation and evidence gaps are visible rather than silently
   converted to neutral/healthy claims.

The pilot can be manual/assisted. Billing, self-serve OAuth, multi-tenant auth,
and public SaaS deployment are not prerequisites for learning whether the
product is useful.

## Do not build now

- additional generic global data providers;
- all five China social adapters in parallel;
- cookie/browser automation as a production source;
- LLM-generated opportunity scores or LLM-generated trend truth;
- microservices/Kubernetes/distributed queues;
- self-serve billing and full SaaS account infrastructure;
- Opportunity Score weight tuning before a labeled relevance/value dataset;
- more social metric taxonomy before one authorized social provider works;
- dashboard polish that does not change the design-partner decision workflow.
