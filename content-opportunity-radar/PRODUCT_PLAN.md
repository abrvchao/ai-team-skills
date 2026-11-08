# Content Opportunity Radar — Product Plan

## Current product mode — Radar Lite（当前产品模式 — 精简版）

Current strategy（当前策略）: **Lite-first, expand from evidence（先精简，再按证据外扩）**.

Radar Lite 默认只保留：

1. Discover（发现）— current stable native discovery（当前稳定原生发现）;
2. Rank（排序）— Top opportunities（Top 机会）;
3. Evidence（证据）— Research Pack + citations（研究包 + 引用）;
4. Handoff（交接）— HANDOFF.json → AI Team Product Contract（产品合同）.

All previously paused expansion work is **Deferred / Restartable（延后 / 可重启）**, not deleted. Canonical restart registry（正式重启清单）: `RADAR_EXTENSIONS.json`.

Lite entrypoint（精简版入口）: `radar_lite.py`; operating guide（使用说明）: `RADAR_LITE.md`.

## Product outcome

Turn real public/first-party signals into explainable, provenance-backed content opportunities that a user can inspect, research and act on.

## Product stages

### R0 Signal foundation

- [x] Public-source providers with failure isolation.
- [x] Durable snapshots/history and provider health.
- [x] Deterministic features/opportunity scoring with provenance.

### R1 Opportunity discovery

- [x] Broad candidate discovery.
- [x] Candidate clustering / deep scan / ranking.
- [x] Persistent collection reuse.
- [x] Chinese Candidate Discovery V1 regression fixed and validated.

### R2 Product-facing workflow

- [x] Research Pack/read model/API.
- [x] Local dashboard/bootstrap path.
- [x] Workspace/domain onboarding.
- [x] Deployable local Docker demo path.

### R3 Legacy validation record（旧验证记录，历史保留）

- [ ] Human precision@5 gate: >= 0.60 on >=20 labeled opportunities.
- [ ] Real GSC-connected workflow exercised with an owned/authorized property.
- [ ] First design-partner/user usefulness session completed and findings recorded.
- [ ] China-social experimental seed path evaluated against human labels before promotion.

> These four checks remain historical truth and are not rewritten retroactively.
> They are no longer treated as one undifferentiated product-value gate.

### R3 Corrected Validation（纠正后的验证模型）

1. **Discovery Quality（发现质量）**
   - 25/25 human judgments complete; frozen binary accounting = 24 labels / 22 unique topics / micro precision@5 = 0.90.
   - Status: **SUPPORTED WITH PROTOCOL MISMATCH（有支持，但协议不匹配）**.
   - The real weak-relevance item remains weak and is not forced into true/false.

2. **Handoff Value（交接价值）**
   - Agent Skills end-to-end Radar → AI Team handoff: **PASS**.
   - Standard HANDOFF.json → Product Contract intake: **2/2 PASS** on Agent Skills + Multi Agent.
   - Status: **PASS（通过）**.

3. **Real Demand / First-party Value（真实需求 / 第一方价值）**
   - GSC first-party workflow is externally blocked until a real owned/authorized property exists.
   - Status: **BLOCKED EXTERNALLY（外部阻塞）**.

4. **Source Expansion（扩源）**
   - Native vs AIHOT / China-social promotion remains experimental.
   - Status: **DEFERRED（延后）** until real demand justifies more source investment.

### R4 Productization decision

- [ ] Decide what remains V1 vs deferred after R3 evidence.
- [ ] Freeze V1 UX/API contract.
- [ ] Produce release/runbook and known-limitations report.

## Current product gate

**R3 Corrected Validation — RADAR LITE / EXPANSION FREEZE（纠正后的 R3 验证 — 精简版 / 外扩冻结）.**

Investment truth（投资真相）: `RADAR_INVESTMENT_GATE.md`.

Action Card human revalidation（行动卡真人复验）已完成，真实 Top-5 **5/5 用户本人仍不知道如何执行**。该结果只证明 execution-knowledge gap（执行知识缺口），不再作为 Radar KILL 条件。

Current decision truth（当前决策真相）:
- Discovery（发现）已有较强真人支持，但旧 binary protocol（旧二元协议）存在 1 个真实弱相关样本导致 completeness mismatch（完整性不匹配）；
- Radar → AI Team Handoff（交接）已经至少一次端到端通过，且标准接入静态复用 2/2 通过；
- 真正缺失的是 first-party demand evidence（第一方真实需求证据），当前受 GSC 外部条件阻塞；
- 当前进入 Radar Lite（精简版）真实使用；Provider / 模型 / UI / scoring 等外扩能力保留为 Deferred / Restartable（延后 / 可重启），由真实 Lite 缺口触发重启。
