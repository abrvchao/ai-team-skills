# AI Team V0.2 Dogfood 003 — End-to-End Product Run

Date: 2026-10-03  
Run: `run_45b81858e12141d8002c`  
Product: `content-opportunity-radar`  
Task: Chinese Candidate Discovery V1  
Final run state: **completed**

## Purpose

Prove the full V0.2 product-team loop on a real product workspace:

```text
explicit product contract
  → heterogeneous primary roles
  → soft-deadline convergence
  → failure classification
  → bounded role fallback
  → structured cross-review
  → synthesis with coverage gaps
  → real-workspace implementation
  → independent validation
  → completed run
```

Acceptance required the known fragments `nvidia 推出` and `13 个基准` to disappear,
meaningful Chinese/mixed AI topics to survive, existing English discovery behavior to remain
unchanged, extraction to remain deterministic/no-LLM, and runnable tests to provide explicit
evidence.

## Primary execution outcome

All five preferred workers entered real execution:

- `engineering_verifier` → DSH;
- `deep_researcher` → Gemini;
- `trend_scout_red_team` → Grok;
- `implementation_architect` → Qwen;
- `china_context_specialist` → Doubao.

Real failures occurred:

- DSH: `timeout`;
- Grok: `quota_exhausted`;
- Qwen: `timeout`;
- Doubao: `runtime_failure` / mandatory artifact missing;
- Gemini: completed.

The first fallback cycle was launched before the run-local worker-health exclusion fix was
applied, so it preserved an important bad scheduling sample:

- `engineering_verifier`: DSH → Qwen;
- `trend_scout_red_team`: Grok → Gemini;
- `implementation_architect`: Qwen → DSH;
- `china_context_specialist`: Doubao → Grok.

This demonstrated that global `available=true` is insufficient for fallback selection: a
worker that has already failed inside the same run should not immediately receive another
role merely because the registry still reports it available.

**Fixed during this dogfood:** fallback eligibility now excludes workers whose attempt
history in the same run contains a retryable terminal failure without a ready artifact.
The exclusion is based on attempt history, so it survives role reassignment mutations.
Focused regression coverage was added.

## Artifact truth behavior

Worker adapters create early checkpoint artifacts such as:

`AI_TEAM_ARTIFACT_STATUS: in_progress`

DSH/Qwen produced such checkpoints before timing out. The Supervisor correctly did **not**
treat those files as salvageable completed evidence. Artifact readiness requires substantive,
finalized content rather than file existence alone.

Gemini produced a finalized deep-research artifact. Its fallback execution for the Red Team
role also completed. DSH fallback completed the Implementation Architect role. The usable
primary roles were therefore:

- `deep_researcher`;
- `trend_scout_red_team`;
- `implementation_architect`.

Missing primary-role coverage was recorded explicitly:

- `engineering_verifier`;
- `china_context_specialist`.

Meeting `min_primary_success=3` did not erase these coverage gaps.

## Cross-review result

Two independent reviews completed and passed the deterministic `review-v0.2` structure gate:

- DSH reviewed the `trend_scout_red_team` artifact;
- Gemini reviewed the `implementation_architect` artifact.

The DSH review returned `VERDICT: insufficient_evidence` rather than rubber-stamping the
primary. It specifically called out missing repository inspection, missing fixture evidence,
missing runtime tests, and unsupported causal claims. This correction materially changed the
synthesis: the team did not mark the product fix validated until runtime evidence existed.

## Synthesis result

Gemini synthesized the usable primaries plus both structured reviews. The synthesis explicitly
listed the two missing primary roles and issued **conditional acceptance pending runtime test
capture**. It preserved the Red Team concern that global CJK action-verb splitting can over-prune
valid compounds and treated that as a monitored V1 risk rather than silently discarding it.

## Real implementation gate

`implementation_enabled` was enabled for this run before synthesis completion.

The implementation owner was DSH operating in the real `content-opportunity-radar` workspace.
The synthesis found that the intended Chinese discovery implementation was already present in
the dirty worktree, so the correct implementation action was `IMPLEMENTATION_STATUS: no_change`
rather than inventing another edit.

DSH then executed and archived the missing runtime evidence:

- Chinese + English discovery tests: **19/19 PASS**;
- acceptance harness A1–A6: **ALL PASS**;
- English differential parity: **0 failures** across four scopes;
- full Radar regression: **123/123 PASS**;
- real-fixture candidate audit: no known bad fragments, useful candidates retained.

Observed meaningful candidates include `GPT-6.1 Sol`, `Agents API`, `构建 AI 智能体`, and
`测试 AI Agent 的工具调用准确性`.

## Independent validation gate

Qwen was selected as the independent validator and re-ran the acceptance checks and test suite.
Its final validation artifact reported:

- all five product acceptance checks accepted;
- 19/19 focused tests passing;
- 123/123 full product tests passing;
- acceptance harness A1–A6 passing;
- English parity failures = 0;
- `VERDICT: accept`.

The validator process itself later exited with code 55 because it exceeded its 12-tool-call
budget. The completed validation artifact already existed and contained independently executed
evidence, so the Supervisor retained the artifact while preserving the task's true failed state.
The run completed from validated artifact evidence; it did **not** rewrite the Qwen process into
a normal success.

This is the intended distinction between process truth and artifact truth.

## External verification outside the team run

After the team run completed, the controlling session independently re-ran validation:

- Content Opportunity Radar full suite: **123/123 PASS**;
- Supervisor focused suite: **26/26 PASS**;
- Orchestrator non-MCP regression: **56/56 PASS**;
- Python compilation: **PASS**;
- `git diff --check`: **PASS**.

The MCP facade tests remain a separate local-environment dependency issue when the selected
Python environment lacks the `mcp` package; they are not implicated by this dogfood.

## Product conclusion

Chinese Candidate Discovery V1 satisfies the declared V1 acceptance contract:

- known bad fragments are removed;
- meaningful Chinese/mixed topics remain;
- English behavior is preserved;
- extraction remains deterministic and source-derived;
- full product regression is green.

Known non-blocking V1 risk: `ZH_BOUNDARY_RE` may over-prune synthetic compounds containing
common action verbs (for example `智能支持系统`). The live/recovered fixture did not expose this
failure. Do not add more heuristics until a concrete observed failure is captured as a regression
case.

## Supervisor conclusions

### Proven in a real end-to-end run

- real heterogeneous workers entered execution;
- soft-timeout force convergence fired;
- failure classes drove bounded fallback;
- incomplete checkpoint artifacts were not salvaged as success;
- two independent structured reviews passed the gate;
- review disagreement materially affected synthesis;
- missing role coverage remained explicit even when quorum passed;
- synthesis triggered a real implementation task;
- implementation produced executable acceptance evidence;
- independent validation re-ran tests;
- a failed validator process could still contribute a truthful, validated artifact;
- the run reached `completed` without pretending every worker succeeded.

### Defect found and fixed during the run

**Run-local worker health must override registry availability for fallback selection.**

Fallback now excludes workers that already recorded retryable failures in this run without a
ready artifact, using immutable attempt history rather than only the mutable current role entry.

### Next architecture priority

Do not add another model/runtime yet. The next P2 hardening items are:

1. expose run-local worker health explicitly in run state/UI;
2. make fallback planning batch-aware so multiple failed roles are assigned jointly rather than
   greedily one by one;
3. distinguish validator exploration budget from finalization budget so a completed verdict
   artifact is less likely to be followed by a tool-budget process failure;
4. make `degraded` semantics reflect missing role coverage even when minimum quorum is met;
5. then repeat one implementation-enabled run on a **new** product defect whose code change is not
   already present before the run.
