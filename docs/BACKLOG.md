# AI Team Backlog

The backlog is ordered. `NOW` items may be implemented. `NEXT` items begin after NOW is accepted. `LATER` items do not pre-empt milestone gates.

## COMPLETE — M1 Control and Resilience（M1 控制与韧性，已完成）

| ID | Item | State | Acceptance |
| --- | --- | --- | --- |
| M1-PROG-001 | Canonical project/product progress model | complete | plan/roadmap/backlog/protocol + run progress + product progress files |
| M1-HEALTH-001 | Explicit run-local worker health | complete | run state shows health and assignment logic consumes it |
| M1-PLAN-001 | Batch fallback role planner | complete | multi-role failure produces one deterministic assignment plan |
| M1-BUDGET-001 | Split exploration/finalization budgets | complete | real DSH/Qwen adapters honor aligned protected finalization |
| M1-REPAIR-001 | Automatic bounded repair loop | complete | repair verdict automatically creates repair + revalidation cycle |
| M1-REPAIR-002 | Repair-stage worker fallback（修复阶段执行器替补） | complete | failed repair worker without ready artifact consumes one bounded attempt and reassigns to another healthy worker |
| M1-BASELINE-001 | Run-start workspace baseline（运行前工作区基线） | complete | validation compares against run-start state, not Git HEAD alone |
| M1-BUDGET-002 | Coding-phase convergence budget（编码阶段收敛预算） | complete | implementation/repair soft deadline 180s; hard deadline remains bounded |
| M1-SYNTH-001 | Zero-tool synthesis evidence handoff（零工具综合证据交接） | complete | forced finalization receives bounded staged primary/review evidence |
| M1-DOGFOOD-004 | M1 graduation dogfood 004（毕业实战 004） | blocked_diagnostic | exposed run-start baseline truth gap |
| M1-DOGFOOD-005 | M1 graduation dogfood 005（毕业实战 005） | complete | repair → independent revalidation → accept completed |
| M1-DOGFOOD-006 | M1 graduation dogfood 006（毕业实战 006） | cancelled_not_needed | Dogfood 005 completed the remaining graduation evidence; avoid redundant token/runtime spend |

## COMPLETE — M2 Real-world execution（M2 真实世界执行，已完成）

| ID | Item | State |
| --- | --- | --- |
| M2-BROWSER-001 | Browser Worker（浏览器执行器） | complete |
| M2-COMPUTER-001 | Local Computer Worker（本机电脑执行器） | complete |
| M2-ANDROID-001 | AndroidDeviceWorker / ADB（安卓设备执行器 / 安卓调试桥） | complete — real OnePlus / Android 16 device passed read-only Bridge E2E with report + screenshot evidence |
| M2-CHINA-001 | Douyin/XHS/Weibo controlled research adapters（抖音/小红书/微博受控实验） | complete |

## COMPLETE — M3 Productization（M3 产品化，已完成）

| ID | Item | State |
| --- | --- | --- |
| M3-DASH-001 | Project Dashboard（项目进度面板） | complete |
| M3-INTAKE-001 | New Project → Product Contract Draft（新项目 → 产品合同草案） | complete |
| M3-PACKAGE-001 | Repeatable installation / packaging（可重复安装 / 打包） | complete |
| M3-RELEASE-001 | Human-readable release report（人类可读发布报告） | complete |
| M3-GRAD-001 | V1 Final Graduation — Daily Delivery Brief（V1 最终毕业 — 每日交付简报） | complete — run `run_48f641fe3489634c37f7`, independent verdict accept, product tests 17/17 PASS, real Computer Worker evidence passed |

M0 / M1 / M2 / M3 均已完成；AI Team V1 已毕业。后续新增核心能力必须由真实交付缺口驱动。

## DEFERRED — after M2 gate

- OpenClaw/Hermes runtime reuse experiment.
- More vendors / Muse.
- Generic memory/chat/skill marketplace.
- Billing/multitenancy/distributed infra.

## Operations（运维）

| ID | Item | State |
| --- | --- | --- |
| OPS-GROK-CRED-001 | Grok secure credential reference（Grok 安全凭据引用） | complete — Secret File + dedicated GROK_HOME + LaunchAgent + Bridge E2E smoke |
