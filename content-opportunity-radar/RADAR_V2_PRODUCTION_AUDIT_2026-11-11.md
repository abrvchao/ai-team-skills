# Radar V2 生产集成独立审计摘要 — 2026-11-11

**结论：BLOCKED（阻塞），当前不能认定为生产集成验收通过。**

## 审计边界

本次审计为只读核验：检查健康接口、工作区/调度读取、V2 数据结构和关键源码路径；未调用写接口、未执行真实扫描、未启用周期调度、未修改生产数据库。所有故障复现均应使用临时 SQLite 与模拟扫描回调。

## 已核验事实

- V1 与 V2 的健康/读取接口可访问，V2 所需四张 tracker 表已存在。
- 默认周期 schedule 处于关闭状态；这条安全默认值必须保持。
- 审计时没有真实 V2 tracker run、diff 或 alert 记录，因此“生产扫描闭环已验证”这一结论不成立。
- 现有历史 V1 run 缺少与 Radar workspace 的关联，首次 V2 扫描只能 bootstrap；至少需要第二次成功扫描才有真实前后变化比较证据。

## 发现与修复门槛

| ID | 级别 | 发现 | 必须达到的修复/验收 |
|---|---|---|---|
| F1 | P1 / High | 失败清理先删除 tracker_diffs，后删除引用它的 tracker_alerts。当 alert 已写入而 complete_run 失败时，外键约束会阻止清理，可能遗留 running run 和 diff/alert。 | 先删除子表 alert，再删除 diff；补充“alert 已成功写入、完成提交失败”的回归测试，确认 run 最终为 failed 且无残留。 |
| F2 | P1 / High | run_now 会把默认关闭的 schedule 永久改为 enabled，手动单次验证可能静默变成每六小时自动采集。 | 手动运行必须是 one-shot（单次执行）；原本关闭的 schedule 在成功、失败或异常后都必须恢复关闭。 |
| F3 | P2 / Medium | run_now 调用全局 tick()，可能顺带执行其他工作区到期任务；结果状态也可能被其他工作区的成功污染。 | 通过目标 schedule_id 精确执行；验证其他工作区没有被触发。 |
| F4 | P2 / Medium | 写接口仅检查客户端 peer 是否为 loopback，没有校验 Host、Origin 和 JSON Content-Type；本机网页可能利用简单跨站请求触发写操作。 | 校验 loopback Host、JSON Content-Type、同源 Origin（存在时）及 Fetch Metadata；覆盖跨站/非 JSON 拒绝测试。 |
| F5 | P3 / Low | workspace 的 scope 变更后，最近成功 run 可能属于旧 scope，缺少显式重置/重新建立基线的操作路径。 | scope 变更时使旧基线失效，并提供可审计的重新 bootstrap 路径。 |
| F6 | P3 / Low | 多种基线/扫描失败原因被折叠为同一错误码，降低诊断能力。 | 保留稳定、脱敏的结构化错误类别。 |
| F7 | Evidence gap | 尚无真实 V2 run/diff/alert 证据。 | 先关闭 F1–F4，再经明确授权做一次受控单次扫描；保持周期 schedule 关闭。 |

## 执行顺序

1. 先修复 F1、F2；不得因此自动启用周期 schedule。
2. 在开放生产写操作前修复 F3、F4，并通过对应负面测试。
3. 在隔离副本/临时数据库上完成回归。
4. 只有在用户明确批准后，才对生产数据库执行一次受控扫描；第二次成功扫描后再评估 diff/alert 质量。
5. 缺少真实运行证据时，状态必须保持 BLOCKED，不得把代码/测试通过描述成生产验收通过。

## 隐私处理

本摘要不包含本机绝对路径、个人邮箱、生产 workspace ID、数据库哈希、PID、密钥或令牌。详细原始审计工件留在本机，不纳入共享快照。


## 2026-11-12 复核后修复状态

在隔离工作区中完成以下代码修复与回归：

- F1 已修复：失败路径先 rollback，按 alert 子表 → diff 父表顺序清理，再将 run 标记 failed；新增 complete_run 失败且 alert 已写入的负面测试。
- F2 已修复：run_now 只执行一次；原本 disabled 的 schedule 在成功/失败/异常后恢复 disabled；已启用的 schedule 不被关闭。
- F3 已修复：scheduler 支持 schedule_id 精确过滤，手动运行不再执行其他工作区的 due schedule。
- F4 已修复：写接口增加 loopback Host、JSON Content-Type、同源 Origin（存在时）和 Sec-Fetch-Site 检查；跨站/非 JSON/伪造 Host 测试均拒绝。
- Radar 全量 Python 回归：**388/388 PASS**；聚焦回归：**53/53 PASS**；Node Worker：**214/214 PASS**；Orchestrator Python：**169/169 PASS**，另有 2 个 MCP 测试模块因环境缺少 mcp SDK 未运行。
- 自动化测试均使用临时 SQLite 和模拟扫描；未写生产数据库、未执行真实扫描、未启用周期 schedule、未调用付费 API。

**当前判定：代码级 F1–F4 修复待独立 DSH 复核；生产验收仍 BLOCKED。** F5/F6 作为 P3 后续项保留，F7 仍需在用户明确批准后做一次受控单次生产扫描，并至少有第二次成功 run 才能验证真实 diff/alert。不得把测试通过表述为生产闭环通过。
