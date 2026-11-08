# AI Team Progress Protocol

## Why this exists

Progress must be visible to the product owner without reading task logs or asking "进度呢？".

There are three nested progress levels:

1. **Program progress** — AI Team V1 milestones.
2. **Product progress** — one product from idea/design through build/validation/release.
3. **Run progress** — one Supervisor execution across primary/review/synthesis/implementation/validation.

## Required communication format

Every substantive progress update must include this compact block, even when the work is primarily technical:

```text
AI Team V1
- M0 Reliable Core: ...
- M1 Autonomous Product Loop: ...
- M2 Real-World Execution: ...
- M3 Productization: ...

Current product
- Product: ...
- Product overall: ...
- Current stage: ...
- Stage progress: ...
- Current blocker: ...
- Next automatic/manual action: ...
```

For long-running work, also state the most recent completed checkpoint and any new risk/failure discovered.

## Product file contract

Every active product repository/directory must contain:

- `PRODUCT_PLAN.md` — stable product stages, deliverables and exit checks.
- `PRODUCT_PROGRESS.md` — current snapshot; updated when a stage starts, completes, blocks, or materially changes.

Recommended product stages:

```text
Discovery → Product Design → Technical Design → Build → Review → Validation → Release
```

A product may rename stages, but it must preserve explicit entry/exit checks.

## Product progress rules

`PRODUCT_PROGRESS.md` must contain:

- overall status and progress;
- every stage with checklist completion;
- current stage;
- active work item(s);
- blockers / missing evidence;
- latest evidence/tests;
- next action;
- last update date.

Percentages are checklist-derived. They are not time estimates.

## Run progress rules

Supervisor runs expose machine-readable `progress` with:

- current phase;
- overall run percent;
- per-phase status/percent;
- task counts;
- blockers/errors and coverage gaps;
- next phase/action.

A phase can be 100% executed even if it completed with missing role coverage. Success/coverage remain separate fields.

## Truth rules

- queued is not running;
- running is not completed;
- file exists is not artifact complete;
- quorum is not full coverage;
- percent complete is not acceptance;
- a failed process may have a valid salvaged artifact, but the process remains failed;
- blocked work must name the blocker rather than silently freezing the percentage.
