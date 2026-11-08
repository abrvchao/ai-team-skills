# AI Team / Radar — Agent handoff (2026-10-15)

> For independent engineering review. This is a snapshot, not a release or acceptance certificate.

## Product and architecture / 产品目标与架构

- **AI Team**: local Bridge (`127.0.0.1:8765`), Supervisor, pull-based workers, old dashboard (`8770`), Dashboard v2 (`8772`). Aim: plan, dispatch, track and cross-review agent work with visible evidence and cost controls.
- **Content Opportunity Radar**: the dogfood product, local UI/API (`8787`), intended to surface source-backed content opportunities, multilingual output, workspaces and research packs.
- Source tree: `ai-team-orchestrator/` and `content-opportunity-radar/` in the **same** `ai-team-skills` Git repository. Local development branch: `feat/radar-product-validation`.
- User priority: **a working Radar page**, not only passing unit tests or architecture documents. User is not a developer; explain steps in Chinese and English terms with Chinese annotations.

## Verified evidence / 已核实证据

- 2026-10-14 23:28 local Radar: `python3 -m unittest discover -p 'test_*.py' -q` => **348 tests, OK** (~5.52 s). Older 2026-10-13 baseline: 314 tests, OK. Tests do **not** prove end-to-end UI or real-user acceptance.
- 2026-10-15 inspection: Bridge `/health` returned `status=ok`, `online_workers=4`, `dsh_available=false`; ports `8770`, `8772`, `8787` returned HTTP 200. HTTP 200 is **not** UI acceptance.
- `source_intake.py` and `test_source_intake.py`: five-platform offline staging entry and malformed authorization-input hardening.
- `r3_readiness.py` and tests: GSC readiness now checks workspace and runtime-token presence as well as report status; no binding to independently verified live execution evidence yet.
- China-social official API review: `ai-team-orchestrator/docs/research/china-social/source-api-capability-20261013.md` — documented only, **no live authorization**.
- Android feasibility: `ai-team-orchestrator/docs/research/china-social/android-collection-feasibility-20261013.md` — Conditional Go; P2 blocked until explicit phone authorization. Prior DSH task `task_1a100a09ec8c8221204f` completed; do not duplicate.
- Dashboard v2 `/api/status` and `ai-team-orchestrator/tasks/china-social-source-expansion-20261012.json` contain progress tracking; compare against primary evidence before trusting status claims.

## Open defects / 当前问题

1. **P0: Radar Lite real-user R3 not passed** (previously 0/4). Verify browser interactions, workspaces, translation, evidence provenance and the actual product flow, not only HTTP and unit tests.
2. **P0: authorization trust boundary**: offline synthetic tests showed `source_intake.py` can accept caller-created `SourceGrant` data without independent trusted-grant lookup; `source_promotion.py` can produce `offline_preview_only` `RawEvent` previews using caller-created grant/review objects. **No demonstrated production persistence**, but must reject forged grants before real promotion. Add trusted authorization registry, provenance and negative tests.
3. **P0: DSH offline**: `deepseek-v4.1-flash` WorkBuddy international free-catalog cache dated 2026-10-13 12:03; 24-hour gate rejects it as `international_catalog_stale`. DSH launchd service not currently loaded, but login-time `RunAtLoad` risk must be checked and safely eliminated. **Do not fake freshness, bypass pricing, or trigger model calls**.
4. **P1: Worker coverage**: Bridge has four online workers but that does not prove they are authorized or free for inference. Two OpenRouter identities are registered as disabled descriptors, not operational worker adapters; keys may exist in user's local `账号管理0909.xlsx` but **must not be published**. No paid model dispatch without explicit budget/model approval.
5. **P1: Android staging**: design evidence-only read-only contracts and proof chain; no phone collection before explicit authorization.

## Hard safety rules / 不可突破的限制

- Grok `ai.team.grok` is disabled. **Never enable it.** Do not call Grok, Volcano, paid models, or unauthorized third-party APIs.
- DSH dispatch requires a freshly independently verified `international WorkBuddy / deepseek-v4.1-flash` **free=true** gate. Catalog refresh is a third-party request and requires explicit user authorization.
- No API keys, access tokens, Excel credential workbooks, `.orchestrator/` secrets, local logs, user data, or generated smoke artifacts in Git commits.
- Check existing Supervisor tasks and artifacts before dispatch to prevent duplicate tasks. Tests should use synthetic/offline fixtures.
- Never describe R3 as accepted or any worker as repaired merely because health checks pass.

## Next actions for reviewing agents / 下一步任务

1. **Security reviewer**: inspect Source Intake → RawEvent boundary; propose trusted-grant registry schema, grant binding to source/account/scope/expiry, review evidence and fail-closed promotion; provide regression tests with forged and expired grants.
2. **Frontend / QA reviewer**: reproduce Radar Lite R3 in a real browser; capture exact failed interactions, browser errors and API traces with secrets redacted. Reconcile Dashboard v2 provenance with test artifacts.
3. **Worker infrastructure reviewer**: read-only audit launchd `RunAtLoad`/`KeepAlive` for DSH/Qwen/WorkBuddy and retry/preflight limits. Propose no-cost fixes. Do not start any paid worker.
4. **Data-source reviewer**: design Android offline staging evidence format (consent, origin, timestamp, hash, device/source provenance, rights constraints); no live scraping.
5. **Integrator**: run full offline regression after each isolated change; report exact commands, test counts, changed files, remaining blockers and whether R3 acceptance actually changed.

## Review response format / 审查反馈格式

For each finding: `severity`, `file:line`, `reproduction`, `impact`, `suggested patch`, `offline test`, `cost/authorization risk`. Separate **observed**, **inferred**, and **not verified**. Prefer small PRs; do not merge or deploy without review.

## Worktree warning / 工作区提醒

At snapshot time the shared repository had ~215 dirty/untracked paths. This handoff deliberately does **not** claim all local changes are committed or uploaded. Reviewers should request an explicit scoped code snapshot and never assume GitHub `main` matches the Mac runtime.
