# Radar Investment Gate（Radar 投资止损门）

Date（日期）: 2026-10-06

Decision（决定）: **LITE-FIRST / EXPANSION FREEZE（精简版优先 / 外扩冻结）**

Canonical decision run（正式决策运行）: `run_94a73384c898cd14ef4b`

## Why（为什么）

- Engineering foundation（工程底座）已具备真实价值：R0–R2 完成，确定性 scoring（评分）/ provenance（来源证据）/ failure isolation（故障隔离）/ Action Card（行动卡）均已存在。
- Relevance（相关性）不等于 product value（产品价值）。现有人工证据中，micro precision@5 = **0.90**，但唯一真实 Design Partner（设计伙伴）会话明确给出 Top-5 **全部 actionable=false（不知道从哪里开始）**。
- Action Card repair（行动卡修复）已经实现，并完成真人复验；结果为 **5/5 用户本人仍不知道怎么执行**。该结果现在被重新解释为 execution-knowledge gap（执行知识缺口），而不是 Radar 核心价值失败。
- R3 继续保持 **0/4**；不以测试通过、相关性高或 preparation ready（准备完成）替代真实用户价值。

## Protocol correction（验证协议纠正）

2026-10-06，真实 Design Partner（设计伙伴）在看完修复后的 5 张 Action Card 后明确回复：

> “12345我都不知道怎么做”

结果：user_knows_manual_execution = 0/5。

此前把 actionable（可行动性）定义为“用户本人知道第一步怎么做”是错误协议。Radar 的核心价值是发现 / 排序 / 证据 / 决策支持；具体执行可以交给 AI Team。

因此此前基于这条结果触发的 KILL/FREEZE（停止 / 冻结）结论撤销，保留 feature freeze（功能冻结），进入 Radar → AI Team handoff（交接）验证。

Evidence（证据）:
- .radar/r3/design-partner/ACTIONABILITY_REVALIDATION.md
- .radar/r3/design-partner/actionability-revalidation.json

## Binding freeze（强制冻结）

自本次止损触发后：

- 不新增 Provider（数据源）；
- 不新增模型；
- 不新增 Dashboard / API / pipeline（面板 / 接口 / 管线）功能；
- 不改变 scoring/ranking（评分 / 排名）公式；
- 不做新的关键词批量人工标注；
- 不重复启动 broad multi-worker research（大范围多执行器研究）；
- Native vs AIHOT（原生 vs AIHOT）暂停，不作为当前花费项；
- GSC（谷歌搜索控制台）没有真实 property（站点属性）时只记录外部阻塞，不反复检查。

## Allowed next actions（允许的后续动作）

Radar 当前只推进 Radar Lite（精简版）最短价值链；外扩能力不删除，全部进入 Deferred / Restartable（延后 / 可重启）清单。

只允许：
1. 用 Radar Lite 持续跑真实机会 → HANDOFF → AI Team 的最短价值链；
2. 只有 Lite 的真实使用暴露具体缺口时，才从 `RADAR_EXTENSIONS.json` 重启对应扩展模块。

## Stop-loss（止损）

- [INVALIDATED AS KILL CRITERION] “用户本人不知道怎么执行”不能单独触发 KILL；它属于 downstream execution-knowledge（下游执行知识）问题。
- 只有 corrected validation（纠正后的验证）证明 Radar 无法稳定发现 / 排序 / 交接有价值机会，才允许进入 KILL/FREEZE。
- 下一动作不能改变 R3 gate（关卡）结论 → **禁止执行**。
- 不允许以“再加一个功能 / 数据源 / Worker 试试”延长生命周期。

## Token / time discipline（Token / 时间纪律）

- 默认使用 **single-worker（单执行器）** 完成单一 gate（关卡）验证。
- 多 Worker（多执行器）只有在“相互独立意见会改变决策”时才允许。
- Worker 失败后，不为凑齐角色自动重复整轮；若已有足够独立证据，允许 degraded（降级）收敛并显式记录 coverage gap（覆盖缺口）。
- 任何 Run（运行）启动前必须写明：它具体解除哪个 gate / blocker（关卡 / 阻塞）。

完整证据与审查记录见：

`content-opportunity-radar/.ai-team-runs/run_94a73384c898cd14ef4b/synthesis/final.md`
