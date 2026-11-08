# AI Team V1 Plan

## Product goal

AI Team V1 is an **AI-native Product Studio** that lets a non-developer provide a product goal, approve major decisions, and receive a runnable, independently validated product result without manually coordinating individual models.

The V1 program is milestone-gated. New ideas do not bypass the current gate unless they are required to unblock it.

## Progress semantics

Progress is a delivery-state measure, not an ETA or quality score.

- A stage percentage is derived from explicit checklist items in this plan.
- `complete` means its exit checks have evidence.
- `in_progress` means implementation exists but the exit gate has not passed.
- `blocked` means a named dependency prevents the next required check.
- `not_started` means no qualifying implementation/evidence exists.
- Overall V1 progress is a weighted roll-up of milestone completion, not a subjective estimate.

Milestone weights:

| Milestone | Weight | Purpose |
| --- | ---: | --- |
| M0 — Reliable AI Team Core | 25% | Heterogeneous workers, Supervisor, evidence/review/implementation gates |
| M1 — Autonomous Product Loop | 35% | Health-aware planning, bounded convergence, repair loop, project progress control |
| M2 — Real-World Execution | 25% | Browser/computer/device workers and China-app experiments |
| M3 — V1 Productization | 15% | Project UX, packaging, release workflow, graduation run |

## M0 — Reliable AI Team Core — COMPLETE

Progress: **5/5 checks complete (100%)**.

- [x] Multiple heterogeneous workers can enter real execution.
- [x] Stable product roles are separated from replaceable workers.
- [x] Supervisor supports timeout convergence, failure classes, artifact salvage, bounded fallback and truthful degraded coverage.
- [x] Independent structured review and evidence/artifact gates are enforced.
- [x] Real-workspace implementation + independent validation completed in Dogfood 003.

Exit evidence: `docs/AI_TEAM_V0_2_DOGFOOD_003.md`.

## M1 — Autonomous Product Loop — COMPLETE（自主产品闭环 — 已完成）

M1 Exit Gate（M1 退出门槛）已由 Dogfood 004 + 005 连续 Graduation Campaign（毕业验证组合）满足。

### M1.1 Run-local Worker Health

Progress: **3/3 checks complete (100%)**.

- [x] Fallback eligibility excludes workers that already had retryable failures in the same run without a ready artifact.
- [x] Run state exposes per-worker `global_status` and `run_local_health` explicitly.
- [x] Supervisor uses the explicit health model for initial availability plus fallback/review/synthesis/implementation/validation assignment.

### M1.2 Batch Role Planner

Progress: **3/3 (100%)**.

- [x] Collect all simultaneously uncovered/failed roles before reassignment.
- [x] Score candidate workers by role fit, run-local health, load, prior attempt and diversity.
- [x] Produce one deterministic assignment plan with provenance before submitting fallback tasks.

### M1.3 Exploration / Finalization Budget

Progress: **4/4 (100%) — real-adapter revalidated after Graduation Dogfood 004.**

- [x] Workers create mandatory artifact checkpoints early.
- [x] Supervisor can force explicit finalization and detect ready-artifact/still-active tasks.
- [x] Supervisor/Worker deadlines share one budget contract derived from the same soft/hard deadlines.
- [x] Finalization consumes protected delivery-only budget in real adapters: Qwen switches from CLI exploration to a no-tools text-only API finalizer with Adapter persistence; DSH completed a bounded native finalization smoke test.

Evidence: `docs/AI_TEAM_V0_3_FINALIZATION_HARDENING.md`.

### M1.4 Automatic Repair Loop

Progress: **4/4 (100%)**.

- [x] `VERDICT: repair` creates a bounded repair task automatically.
- [x] Repair task receives failed checks, validation evidence and the exact original acceptance contract.
- [x] A worker different from the repair owner independently re-validates each repair.
- [x] The loop terminates after at most two repair attempts with truthful `accept`, `blocked`, or `failed` state.

### M1.5 Project Progress Control Plane

Progress: **4/4 (100%)** after the initial progress-control implementation.

- [x] Canonical AI Team plan / roadmap / backlog exist.
- [x] Every product has a required plan and progress file convention.
- [x] Team runs expose phase-level machine-readable progress.
- [x] Communication protocol requires total-plan + product-stage progress on every substantive update.

### M1.6 Graduation Dogfood

Progress: **4/4 (100%)** — Dogfood 004 + 005 连续毕业验证已覆盖全部真实运行证据。

Run a new defect/product task whose accepted code change is **not already present** before the run.

- [x] At least three independent product roles contribute useful artifacts.
- [x] At least one worker fails and is health-aware/batch-reassigned.
- [x] At least one `repair → revalidation` cycle occurs automatically.
- [x] Product reaches runnable acceptance without manual orchestration of individual workers.

### M1 Exit Gate

M1 is complete: M1.1–M1.6 全部完成，毕业证据归档于 `AI_TEAM_V0_3_DOGFOOD_004.md` 与 `AI_TEAM_V0_3_DOGFOOD_005.md`。

## M2 — Real-World Execution — COMPLETE（真实世界执行 — 已完成）

Progress: **4/4 checks (100%)**.

- [x] Browser Worker contract + real task（浏览器执行器合同 + 真实任务）.
- [x] Local Computer Worker contract + real task where CLI/API is insufficient（本机电脑执行器 + GUI 真实任务）.
- [x] AndroidDeviceWorker with ADB/read-only automation and evidence capture（安卓设备执行器；真实 OnePlus / Android 16 真机经 Bridge 完成 report + screenshot 只读端到端验收）.
- [x] Controlled Douyin/Xiaohongshu/Weibo personal-research experiments with normalized provenance（抖音/小红书/微博受控实验 + 标准化来源记录）.

M2 starts only after M1 Exit Gate.

## M3 — V1 Productization — COMPLETE（V1 产品化 — 已完成）

Progress: **5/5 checks (100%)**.

- [x] Project dashboard/control plane shows plan, stages, workers, blockers, artifacts and next action（项目面板展示计划、阶段、执行器、阻塞、成果与下一动作）.
- [x] New Project flow turns a vague goal into a proposed Product Contract for user approval（新项目流程：一句话想法 → 产品合同草案 → 明确批准后才启动）.
- [x] Installation/packaging path is documented and repeatable（安装 / 验证 / 卸载脚本可重复执行，且不读取或删除密钥）.
- [x] Release artifact/report is understandable without reading raw task logs（发布报告从 run state / validation / repair artifacts 确定性生成）.
- [x] V1 graduation: Daily Delivery Brief（每日交付简报）run `run_48f641fe3489634c37f7` reached runnable validated output through Product Contract → Delivery Plan → Worker fallback → Review → Implementation → Validation → Release Report, followed by real Computer Worker read-only GUI evidence.

## Current program order

```text
M0 Reliable Core              COMPLETE
        ↓
M1 Autonomous Product Loop    COMPLETE
        ↓
M2 Real-World Execution       COMPLETE
        ↓
M3 V1 Productization          COMPLETE
        ↓
AI Team V1 graduation         COMPLETE
```

## Scope after M1 Gate（M1 关卡后的范围）

Do not prioritize:

- more model vendors or Muse;
- generic memory/chat gateways/skill marketplace;
- Android / China-app automation only in the M2 planned order;
- OpenClaw/Hermes runtime adapter work;
- billing/multitenancy/distributed infrastructure;
- UI polish that does not expose the canonical project/run progress model.
