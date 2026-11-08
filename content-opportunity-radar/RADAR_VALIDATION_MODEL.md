# Radar Validation Model（Radar 验证模型）

Date（日期）: 2026-10-06

## What Radar is supposed to do（Radar 到底负责什么）

Radar 的核心职责不是让用户亲自学会执行每个机会，而是：

1. 从多源信号中发现候选机会；
2. 判断候选是否属于目标范围；
3. 给出证据、热度、置信度和排序；
4. 帮用户判断“这个机会值不值得追”；
5. 把值得追的机会交给 AI Team 继续做成内容 / 产品 / 研究 /行动方案。

因此，“用户本人会不会做”不是 Radar 核心价值的唯一或最终标准。

## Four different validation layers（四层验证必须分开）

### Layer 1 — Relevance（相关性）

问题：

> 这个候选和目标领域到底相关不相关？

对应之前的 strong / medium / weak / unrelated（强 / 中 / 弱 / 不相关）。

它验证的是 Radar 的 discovery precision（发现精度）。

### Layer 2 — Opportunity value（机会价值）

问题：

> 这个机会值不值得我关注、继续研究或让 AI Team 去做？

对应 usefulness（有用程度）和 would_act（是否愿意推进）。

它验证的是 Radar 的 ranking / prioritization（排序 / 优先级）价值。

### Layer 3 — Handoff readiness（交接就绪度）

问题：

> Radar 给的信息是否足够让我做出“继续 / 放弃 / 交给 AI Team”的判断？

这才是 Radar 输出层真正应该负责的 actionability（可行动性）。

它不要求用户本人掌握具体制作方法。

### Layer 4 — Execution（执行）

问题：

> 这个机会具体怎么做成内容、产品、报告或行动？

这是 AI Team / downstream worker（下游执行系统）的责任，不应该拿来单独否定 Radar。

## Protocol correction（协议纠正）

此前把 actionable（可行动性）解释为：

> “用户本人是否知道第一步怎么做”

这是错误定义。

用户对 Top-5 回答“都不知道怎么做”，只能证明：

> 用户本人缺少这些主题的手工执行路径。

不能证明：

> Radar 没有发现有价值的机会。

尤其是同一真实会话中，多个主题曾被用户给出高 usefulness（有用程度）与 would_act（愿意推进）信号，这说明“值得关注 / 愿意推进”和“自己会不会做”是两个不同变量。

## Correct next validation（正确的下一步验证）

不再要求用户继续给关键词打分，也不再要求用户亲自知道怎么执行。

正确测试是：

> Radar 发现一个高价值机会后，AI Team 能否接手，把它自动转成一个可直接审阅的具体交付物；用户只需要判断“值不值得继续”。

这叫 Radar → AI Team Handoff Test（Radar → AI Team 交接测试）。

在这个测试完成前：

- 保留 feature freeze（功能冻结）；
- 不增加 Provider / 模型 / UI / scoring；
- 不做新的关键词批量标注；
- 不把“用户本人不会做”作为 KILL（停止）条件。
