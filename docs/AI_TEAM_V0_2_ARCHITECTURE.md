# AI Team V0.2 Architecture

Status: implementation target after the positioning reset.

## Objective

V0.2 proves that AI Team is a resilient heterogeneous product team, not a parallel prompt launcher.

## Layer boundaries

```text
Product Intent
  idea • goal • constraints • acceptance • user checkpoints
        ↓
AI Team Product Supervisor                         [WE OWN]
  decomposition • role fit • budgets • gates • recovery
  cross-review • synthesis • coverage/provenance
        ↓
Worker Contract                                    [WE OWN]
  capability • status • progress • artifacts • failure class
        ↓
Execution Runtimes                                 [BORROW/ADAPT]
  OpenClaw • Hermes • CLI • API • WebCodex • browser • device
        ↓
Real Environments                                  [REAL WORLD]
  git • tests • web • APIs • files • Android • China apps
```

The Supervisor must never depend on one runtime's private internal concepts. Runtime-specific integration belongs behind worker adapters.

## Product task contract V0.2

Every primary assignment must define:

```json
{
  "objective": "one outcome",
  "role": "why this worker is selected",
  "deliverables": ["expected artifact paths/types"],
  "evidence_required": ["what must support the answer"],
  "acceptance": ["binary/verifiable checks"],
  "stop_conditions": ["when exploration must end"],
  "budget": {
    "soft_deadline_seconds": 0,
    "hard_deadline_seconds": 0
  },
  "fallback_policy": "reassign|salvage|fail_closed"
}
```

The contract is more important than the prompt wording.

## Supervisor state machine V0.2

```text
PLAN
 ↓
PRIMARY_RUNNING
 ├─ success -----------------------------┐
 ├─ soft deadline → FORCE_CONVERGE       │
 ├─ worker failure → FALLBACK/REASSIGN   │
 └─ hard deadline → CANCEL + SALVAGE     │
                                        ↓
PRIMARY_GATE
 ├─ enough validated coverage → REVIEW_RUNNING
 └─ insufficient but useful work → DEGRADED_SYNTHESIS

REVIEW_RUNNING
 ├─ independent review artifacts
 ├─ bounded repair if acceptance fails
 └─ missing reviewer → replacement/degraded coverage
                                        ↓
REVIEW_GATE
                                        ↓
SYNTHESIS
                                        ↓
FINAL
  + evidence coverage
  + missing roles
  + unresolved disagreements
  + artifacts
  + next product decision
```

## Required resilience mechanisms

### Force convergence
At a soft deadline, one bounded follow-up may instruct the worker to stop new exploration and emit the mandatory artifact from current evidence.

### Artifact salvage
If a worker process fails or is cancelled after creating the expected artifact, the Supervisor may validate and retain that artifact. Salvage must be explicit in provenance and must not silently turn a failed task into normal success.

### Failure classification
Workers should expose machine-readable classes where possible: auth required, quota exhausted, rate limited, model unavailable, timeout, artifact missing, and runtime/tool failure.

### Reassignment
A failed **role** can be reassigned to another available worker. Reassignment must preserve the original role brief and record the fallback worker.

### Degraded synthesis
When full quorum is impossible, synthesis may continue only if policy allows it. The result must state exactly which roles and reviews are missing.

## Cross-review contract

Reviewers receive the primary artifact, original acceptance criteria, relevant evidence references, and their review role. They do not need the primary worker's full conversation.

A review artifact should contain:

```text
ACCEPTED_CHECKS
FAILED_CHECKS
COUNTER_EVIDENCE
UNSUPPORTED_CLAIMS
REQUIRED_REPAIR
VERDICT: accept | repair | insufficient_evidence
```

The verdict is about artifact quality against the contract, not which model is “better”.

## Runtime integration strategy

OpenClaw/Hermes may eventually provide commodity execution capabilities. Integration should happen through thin adapters rather than importing their architecture into Supervisor logic.

Examples:

```text
OpenClawRuntimeWorker
HermesRuntimeWorker
WebCodexWorker
DirectAPIWorker
AndroidDeviceWorker
```

All must converge on the same worker contract.

## V0.2 success criteria

V0.2 is not complete until all are demonstrated on a real product run:

1. **Role diversity** — at least three materially different worker roles contribute independent artifacts.
2. **Truthful provenance** — final output names contributing and missing roles.
3. **Cross-review** — at least two independent review artifacts pass through a review gate before final synthesis.
4. **Soft-timeout convergence** — a deliberately slow worker is nudged to produce a bounded artifact before hard cancellation.
5. **Failure recovery** — a deliberately quota-failed/unavailable worker role is reassigned or explicitly degraded without collapsing the entire run.
6. **Artifact salvage** — a valid artifact created before process failure can be retained with truthful salvage status.
7. **Real environment evidence** — at least one worker executes tests or another real check rather than reasoning only.
8. **Product outcome** — the run changes a real product artifact and passes declared acceptance tests.

## V0.2 roadmap

### P0 — Supervisor resilience — IMPLEMENTED, NEEDS REAL-RUN DOGFOOD
- soft deadline → force convergence;
- structured worker failure classes;
- artifact salvage state;
- bounded role fallback/reassignment;
- degraded synthesis with coverage report.

### P1 — Product contract and review gates — IMPLEMENTED, NEEDS REAL-RUN DOGFOOD
- V0.2 product task contract is stored in run/task metadata;
- roles are stable identities separate from preferred/current workers;
- review artifact schema is deterministically validated;
- malformed reviews do not satisfy the review gate;
- synthesis receives acceptance checks and coverage gaps.

Remaining P1 follow-up: richer acceptance evaluators beyond deterministic artifact/review structure checks.

### P2 — Dogfood on Content Opportunity Radar — REAL E2E COMPLETE
- Dogfood 001 proved resilient primary/review/synthesis behavior and exposed salvage/fallback/deadline defects;
- Dogfood 002 exercised the new implementation path but failed upstream at worker convergence;
- Dogfood 003 (`run_45b81858e12141d8002c`) reached the full graph: primary → fallback → two structured reviews → synthesis → real-workspace implementation → independent validation → completed;
- Chinese Candidate Discovery V1 now has 123/123 Radar tests passing, acceptance harness A1–A6 passing, and English parity failures = 0;
- external post-run verification also confirmed Supervisor 26/26 and Orchestrator non-MCP 56/56 regressions green.

See `docs/AI_TEAM_V0_2_DOGFOOD_001.md`, `docs/AI_TEAM_V0_2_DOGFOOD_002.md`, and `docs/AI_TEAM_V0_2_DOGFOOD_003.md`.

### P2.5 — Implementation / Validation Gate — IMPLEMENTED AND REAL-RUN EXERCISED
- `implementation_enabled` extends synthesis into a bounded real-workspace implementation phase;
- implementation forbids automatic commit/push and supports `changed`, `no_change`, or `blocked`;
- an independent worker must re-run acceptance evidence and produce a structured verdict;
- Dogfood 003 completed this path: DSH performed real-workspace runtime verification and Qwen independently re-ran the acceptance/test suite;
- Qwen later exceeded its tool-call budget, but its already-complete `VERDICT: accept` artifact was retained without rewriting the failed process as success.

### P2.6 — Worker Convergence Contract — PARTIALLY IMPLEMENTED, REAL-RUN EXERCISED
- mandatory artifact checkpoints are created early;
- force-convergence enters explicit bounded finalization;
- Supervisor detects `artifact ready + worker still active` and requests immediate completion;
- phase cycles receive independent convergence budgets;
- Dogfood 003 proved these controls across primary, review, synthesis and validation;
- remaining hardening: separate exploration/finalization tool-call budgets, expose run-local worker health explicitly, and plan multiple fallback assignments jointly instead of greedily.

### P3 — Real-world device worker
- AndroidDeviceWorker capability contract;
- ADB/UI automation feasibility;
- controlled Douyin/Xiaohongshu experiments;
- normalized artifacts and provenance.

### P4 — Runtime reuse experiment
After V0.2 core works, prototype one commodity runtime adapter to OpenClaw or Hermes. Measure whether it removes code/maintenance without weakening AI Team's truth and artifact contracts.

## Explicitly deferred

- Muse integration;
- more worker vendors;
- general memory;
- general chat gateways;
- skill marketplace;
- SaaS/billing/multitenancy;
- broad UI work;
- distributed infrastructure.
