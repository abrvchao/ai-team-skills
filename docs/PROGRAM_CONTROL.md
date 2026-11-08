# AI Team Program Control Board（AI Team 项目总控板）

Last updated（最近更新）: 2026-10-06

## Program clock（项目时钟）

- Start（开始）: 2026-09-16
- Current date（当前日期）: 2026-10-06
- Elapsed（已过去）: 20 days（20 天；跨 21 个自然日）
- North Star（北极星）: 让非开发者只做关键产品决策，AI Team 自动完成研究、实现、真实环境执行、独立复验和可运行交付。

## One-screen status（一屏状态）

| Workstream（工作流） | Progress（进度） | State（状态） | Blocker（阻塞） | Next action（下一动作） |
| --- | ---: | --- | --- | --- |
| M0 Reliable Core（可靠核心） | 5/5 = 100% | complete（完成） | none | freeze（冻结） |
| M1 Autonomous Product Loop（自主产品闭环） | 22/22 = 100% | complete（完成） | none | freeze（冻结） |
| M2 Real-World Execution（真实世界执行） | 4/4 = 100% | complete（完成） | none | freeze（冻结） |
| M3 V1 Productization（V1 产品化） | 5/5 = 100% | complete（完成） | none | freeze（冻结） |
| AI Team V1 Final Graduation（V1 最终毕业） | complete | graduated（已毕业） | none | use V1 on real goals（用于真实目标） |
| Content Opportunity Radar（内容机会雷达） | Lite local product = operational; full UI i18n = 8 languages; content auto-translation follows selected language; Radar 190/190 PASS | Start → Language → Scan → Translate/Inspect → Handoff real E2E PASS（真实端到端通过） | none for Lite core（精简核心无阻塞） | compare standalone AIHOT vs Radar on matched scopes; no automatic provider promotion |

Weighted AI Team V1 roll-up（AI Team V1 加权进度）: **100%**.

## Critical path（关键路径）

The original delivery promise is now proven（最初的交付承诺已验证）:

1. [COMPLETE] Delivery Plan（交付计划）— goal → phases / workers / dependencies / due time
2. [COMPLETE] Daily Delivery Brief（每日交付简报）graduation run（毕业运行）
3. [COMPLETE] Independent validation + release report（独立验收 + 发布报告）
4. [COMPLETE] Real Computer Worker evidence（真实本机电脑执行证据）
5. [COMPLETE] AI Team V1 graduation（AI Team V1 最终毕业）
6. [OPERATE] Use V1 on real goals; Radar R3 continues as dogfood（用 V1 承接真实目标；Radar R3 继续内部实战）

## Parallel lanes（并行泳道）

### Lane A — Critical-path product validation（关键路径产品验证）

Active（进行中）: Goal Runner operational use（目标执行入口真实使用）; the next real goal should enter through one unified goal → plan → approve/start → delivery path.

Rule（规则）: always receives the first available execution slot.

### Lane B — Leverage / acceleration（复用与提速）

Active（进行中）: Ponytail third-party skills（Ponytail 第三方技能） for minimal diffs, over-engineering review, and debt visibility.

Rule（规则）: may simplify work, but cannot redefine product gates or truth rules.

### Lane C — External wait states（外部等待）

- Verified GSC property（已验证 GSC 站点）

Rule（规则）: waiting dependencies stay visible but consume no engineering slot.

## WIP limit（在制品限制）

- Maximum active code-changing streams（同时改代码的工作流）: **2**
- Maximum active product-validation stream（产品验证流）: **1**
- External blockers（外部阻塞） remain tracked but do not block independent work.
- New ideas enter BACKLOG（待办池） unless they directly close the active gate.
- Experimental / unvalidated products（实验型 / 未验证产品）必须在扩范围前写明 Stop-loss（止损）.
- Repeated broad multi-worker runs（重复大范围多执行器运行）必须明确它将解除哪个 gate / blocker（关卡 / 阻塞），否则禁止启动.
- Prefer minimum sufficient workers（优先最少充分执行器）；不为“凑齐角色”额外消耗模型预算.

## Turn-by-turn execution contract（每轮执行合同）

Every substantive update must contain:

1. AI Team program progress（AI Team 总进度）
2. Current product progress（当前产品进度）
3. Current stage / gate（当前阶段 / 关卡）
4. What completed since last update（本轮完成）
5. Blocker（阻塞）
6. Next automatic action（我自动继续做什么）
7. Next user decision/input only when truly required（只有确实需要时才让用户决策/输入）

## Stop-doing list（停止事项）

Until the active gates close, do not prioritize:

- more model vendors（更多模型厂商）
- Muse / generic agent platform expansion（Muse / 通用代理平台扩展）
- OpenClaw/Hermes adapter work（OpenClaw/Hermes 适配）
- generic memory/chat/marketplace（通用记忆/聊天/市场）
- billing / multitenancy / scaling（计费 / 多租户 / 扩容）
- UI polish not tied to acceptance（与验收无关的 UI 打磨）

## Immediate execution queue（立即执行队列）

1. [COMPLETE] V1 Final Graduation（V1 最终毕业）: Daily Delivery Brief completed with independent `accept`.
2. [ACTIVE] Operate V1 through Goal Runner（通过目标执行入口使用 V1）: one sentence goal → Product Contract → Worker plan → Delivery Plan → Schedule → approve/start → Validation → Delivery, without manual Worker micromanagement.
3. [OPERATE / LITE-FIRST] Radar Lite local product: one-command local entry + full 8-language Dashboard i18n + cached content auto-translation + Scan + Evidence + deterministic Handoff are implemented; Radar regression **190/190 PASS**. AIHOT standalone comparison is reactivated, but provider integration remains gated on real comparison evidence.
4. [WAITING] GSC first-party（GSC 第一方）: execute immediately when a verified property becomes available.
5. [COMPLETE] AndroidDeviceWorker（安卓设备执行器）: real-device Auto-E2E passed with report + screenshot evidence.

## Truth rule（真相规则）

Plan exists ≠ progress. Code exists ≠ accepted. Preparation exists ≠ gate passed. A blocked gate remains blocked until its exact evidence exists.
