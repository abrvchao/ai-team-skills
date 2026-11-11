# AI Team Repository Instructions

## Product identity

This repository builds an **AI-native Product Studio**: a heterogeneous AI team that turns vague ideas into working products through research, implementation, real-environment execution, independent review, evidence/artifact gates, and resilient iteration.

Read `docs/AI_TEAM_POSITIONING.md`, `docs/PROGRAM_CONTROL.md`, `docs/AI_TEAM_V1_PLAN.md`, `docs/ROADMAP.md`, `docs/BACKLOG.md`, and `docs/PROGRESS_PROTOCOL.md` before architecture or roadmap changes.

## Architecture boundary

AI Team core owns:
- heterogeneous model role specialization;
- Product Supervisor orchestration and recovery;
- task/artifact/evidence contracts;
- independent cross-review and acceptance gates;
- product-run provenance;
- thin adapters to real-world execution surfaces.

Prefer borrowing/integrating mature external runtimes for generic agent loops, personal memory, skill loaders/marketplaces, chat gateways, browser/terminal wrappers, cron, and provider-normalization plumbing.

Do not add a core feature merely to match OpenClaw, Hermes, or another general-purpose agent platform.

## Required pre-change check

Before implementing a new core feature, state:
1. how it improves idea → working-product convergence;
2. which owned capability it strengthens;
3. why a thin integration with an existing runtime is insufficient.

If those answers are weak, do not expand the core.

## Current priority

**AI Team V1 is graduated（AI Team V1 已毕业）**.

1. Browser Worker（浏览器执行器）— complete；
2. Local Computer Worker（本机电脑执行器）— complete；
3. AndroidDeviceWorker（安卓设备执行器）— complete；real-device Bridge E2E + report + screenshot evidence passed；
4. China App controlled experiment（中国 App 受控实验）— complete；
5. V1 Final Graduation（V1 最终毕业）— complete；evidence: `docs/AI_TEAM_V1_GRADUATION.md`。

Post-V1 priority（V1 后优先级）: use the delivery system on real user goals first. Do not expand the core with OpenClaw/Hermes adapters, more vendors, Muse, generic memory/chat/marketplace, billing, multitenancy, or infrastructure scaling unless real delivery evidence identifies a concrete gap.

## Truth rules

- queued/acknowledged is not running;
- missing artifact is not completion;
- salvaged artifact is not normal success;
- degraded synthesis must list missing roles/reviews;
- never present partial worker coverage as team consensus.

## Progress reporting contract

Progress is part of the product contract, not optional narration.

- Every active product must contain `PRODUCT_PLAN.md` and `PRODUCT_PROGRESS.md`.
- Update product progress when a stage starts, completes, blocks, or materially changes.
- Every substantive user/status update must include AI Team program progress plus the current product/stage progress, blocker, and next action.
- Percentages must come from explicit checklist/stage completion and must not be presented as time estimates or acceptance scores.
- Supervisor run progress must remain separate from coverage and acceptance: 100% execution can still end blocked, degraded, or needing repair.

## Language and learning convention

- User-facing English terms, status names, milestone names, and architecture labels must include an adjacent Chinese explanation/translation.
- For new or materially changed non-trivial code, add concise Chinese comments around important control-flow, truth-safety, recovery, scheduling, or boundary logic.
- Do not add noisy comments for obvious syntax; comments should explain why the code exists, what invariant it protects, or what failure mode it handles.
- Machine-facing identifiers, protocol keys, filenames, and code symbols remain in English for compatibility; explain them in Chinese when presenting them to the user.
