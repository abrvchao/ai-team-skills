# AI Team V0.2 Dogfood 001 — Chinese Candidate Discovery

Date: 2026-09-30  
Run: `run_df0ffe97cfa89d0142ad`  
Product: `content-opportunity-radar`  
Status: **completed, degraded coverage, product change accepted**

## Why this run existed

This was the first real dogfood of the V0.2 Product Supervisor against an
actual product decision rather than a synthetic orchestration test.

The product question was whether the uncommitted Chinese Candidate Discovery V1
implementation should be accepted, repaired, or rejected after the prior AIHOT
run emitted low-quality fragments such as `nvidia 推出` and `13 个基准`.

The run used explicit product acceptance, evidence, stop, and fallback contracts.
Primary roles were:

- `engineering_verifier` → preferred worker DSH;
- `deep_researcher` → preferred worker Gemini;
- `trend_scout_red_team` → preferred worker Grok;
- `implementation_architect` → preferred worker Qwen;
- `china_context_specialist` → preferred worker Doubao.

## Product conclusion

**Accept Chinese Candidate Discovery V1 as the current V1 implementation.**

Independent engineering verification and cross-review confirmed:

- the original public-source bad fragments are removed;
- recovered meaningful topics are retained;
- the frozen ten-event AIHOT fixture retains at least five useful candidates;
- observed-span extraction is deterministic and contains no LLM topic invention;
- existing English discovery behavior does not regress;
- the full Radar suite passes on Python 3.12;
- no opportunity-score tuning was introduced.

The strongest engineering artifact independently ran 122/122 Radar tests and
reproduced the old HEAD failure before verifying the new behavior.

## Important non-blocking product risks

The run preserved concrete limitations rather than hiding them:

- clause-leading quantity filtering does not catch every mid-phrase quantity or
  action fragment;
- some run-on Chinese clause spans remain possible;
- English-title + Han-summary input changes extraction granularity;
- some single-token versioned names can still be filtered;
- final candidate → evidence-event assignment can include an event that does not
  itself contain the canonical phrase, as long as another cited event does;
- `seed_source_mode` changed from `live` to `live:<seed_mode>` and consumers
  should not silently assume exact `live` equality.

These are not blocking for the stated V1 contract. New heuristics should still
require a concrete observed production failure plus a regression case.

## What the Supervisor proved in a real run

### PASS — truthful worker state

All five primary tasks genuinely entered `running`; the run did not confuse
queued/ACK state with execution.

### PASS — soft-deadline convergence

At the configured primary soft deadline, the Supervisor sent bounded
force-convergence messages to still-active workers instead of only recording a
warning.

### PASS — hard timeout and salvage

At the hard deadline the Supervisor cancelled stalled work. Qwen had already
written its required `primary.md`, so the artifact was salvaged and retained
without rewriting the cancelled task into a normal success.

Both review workers later produced `review.md` before terminal completion; the
review artifacts were also salvaged after timeout and passed the deterministic
`review-v0.2` structure gate.

### PASS — role fallback exists

Real fallback events occurred:

- `deep_researcher`: Gemini → Qwen after timeout;
- `trend_scout_red_team`: Grok → Gemini after timeout;
- `china_context_specialist`: Doubao → Qwen after runtime/artifact failure.

Original worker/task/failure provenance remained in run state.

### PASS — degraded synthesis is truthful

Only two primary role artifacts were usable at the primary gate:

- `engineering_verifier`;
- `implementation_architect`.

The run continued in degraded mode and explicitly recorded missing roles:

- `deep_researcher`;
- `trend_scout_red_team`;
- `china_context_specialist`.

The final synthesis did not claim five-role consensus.

### PASS — independent structured review

Two cross-reviews were produced and validated through the review contract:

- Qwen reviewed the DSH engineering artifact;
- DSH reviewed the Qwen architecture artifact.

The DSH review materially corrected the Qwen record, including Python-version
evidence, quantity-filter scope, mixed-language behavior, and source-mode risks.

## Defects discovered by dogfood

### 1. Late artifact from a superseded attempt could be lost

The original Gemini attempt wrote a 13 KB `primary.md` after the role had already
been reassigned. Because the role entry pointed only at the fallback workspace,
the existing salvage path could not discover the original artifact.

This was serious: the late Gemini artifact had a `REPAIR` verdict. It happened
to survive indirectly because DSH's cross-review referenced it, but the
Supervisor itself would otherwise have omitted a real minority position.

**Fixed after the run:** artifact discovery now scans all role attempts,
retains attempt-level provenance, can salvage a late earlier artifact, and
stages non-canonical attempt artifacts as comparison evidence.

### 2. Fallback selection created worker hotspots

Multiple failed roles could select the same available fallback worker. In the
real run Qwen became fallback for both deep research and China context, creating
queue serialization and reducing heterogeneity.

**Fixed after the run:** fallback selection now prefers the lowest-loaded
eligible worker within the role's ordered fallback set.

### 3. Deadline/salvage bookkeeping was not cycle-aware/idempotent

The real run showed repeated hard-timeout errors and duplicate salvage warnings
across primary cycles.

**Fixed after the run:** soft/hard deadline bookkeeping is keyed to the current
phase cycle, fallback cycles can receive their own convergence budget, timeout
failure class is written into attempt provenance, and salvage is idempotent.

## Defect still open: no implementation / merge phase

The current team graph is:

```text
primary → cross-review → synthesis
```

It can decide whether a product change is acceptable, but it does not yet own a
safe final execution phase that applies an accepted repair to the real product
workspace and re-runs declared acceptance tests before merge.

For this dogfood, the candidate product implementation already existed in the
dirty worktree before the run, so the team validated it rather than creating it.
Therefore the V0.2 success criterion "the run changes a real product artifact"
is **not yet fully satisfied by the Supervisor graph itself**.

The next core architecture addition should be a bounded execution gate:

```text
synthesis decision
  → implementation task
  → real product workspace / branch
  → declared test gates
  → independent code/result review
  → accept/reject merge artifact
```

This is more important than adding another model or runtime adapter.

## Validation after dogfood fixes

- `test_team_supervisor.py`: **12/12 PASS**
- Orchestrator non-MCP Python regression: **42/42 PASS**
- Content Opportunity Radar: **122/122 PASS**
- Grok worker: **10/10 PASS**
- Python compile / diff checks passed in the V0.2 refactor session.

The two MCP facade tests remain environment-blocked only when using the local
Python 3.12 environment without the `mcp` package; this predates the dogfood
fixes and is not a Supervisor regression.

## V0.2 status after Dogfood 001

P0 Supervisor resilience: **implemented and real-run exercised**, with the
post-run fixes above requiring one more real validation cycle.

P1 Product contract and review gate: **implemented and real-run exercised**.

P2 Product dogfood: **completed as a decision/review run**, but the product-run
implementation/merge success criterion remains open because the Supervisor has
no execution phase yet.

Next core priority: **Implementation / Merge Gate**, then a second dogfood run
where the team itself produces and validates the product change end to end.
