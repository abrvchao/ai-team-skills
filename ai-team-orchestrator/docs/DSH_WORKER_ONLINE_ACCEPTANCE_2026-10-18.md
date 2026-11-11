# AI Team DSH｜上线及真实接单验收（2026-10-18）

**结论：PASS，DSH 已在本机 Bridge 注册为真正在线的 `registered_pull` Worker，完成了一个小型真实任务。**

## 前提与安全约束

- 用户于本轮明确授权「DSH 上线验收」。
- 隔离环境保持 `dsh-workbuddy-connect=0.7.6`、`@deepseek-ai/dsh=0.2.0-rc.2`，没有升级至 0.7.7/0.7.8。
- 启动前 `assertInternationalFreeEligible()` 验证通过：来源 `workbuddy-ai:app`、模型 `deepseek-v4.1-flash`、标价 `billing.free=true`、`credits=x0.00`，具有有效零倍率促销；价格目录缓存小于 24 小时。
- Grok 保持 `disabled`。并非「零费用保证」：上游政策可能变化，生产任务仍依赖每轮费用门禁和周期复查。
- 启动前 Bridge 数据库确认没有旧的未完成 DSH 任务，避免上线自动领取历史积压任务。
- 仅使用 `launchctl kickstart` 手动启动一次；`RunAtLoad=false`、`KeepAlive=false`，不会登录自动重启。进程退出后须重新确认安全资格才手动恢复。

## 真实上线验证

| 检查项 | 实际观测 |
|---|---|
| LaunchAgent | `ai.team.dsh` 运行中，进程 PID `15020` |
| Bridge `/health` | `status=ok`，`dsh_available=true`，`online_workers=5` |
| Bridge `/agents` | `dsh`，`transport=registered_pull`，`available=true`，`queueable=true` |
| 注册的 Worker | `worker_c885ec234d66aeb697df` |
| 心跳 | 2026-10-17T19:17:07Z 最近心跳，核验时约 0.8 秒前 |
| 启动预检 DSH 会话 | `turn/end.reason.kind=completed`，`assistant/message.source.provider=workbuddy-ai`、`source.model=deepseek-v4.1-flash` |

## 一项真实任务验收

- Bridge `POST /tasks` 提交仅针对 `agent_id=dsh`，没有触发 push-local 后备进程：Bridge 路由是 `registered_pull`。
- 任务 `task_80e3a5e50db1158008d2`：在工作区 `ai-team-dsh-acceptance-20261018-viy6alxi` 内创建一个 `dsh-worker-acceptance.md`，包含精确一行 `DSH_WORKER_ACCEPTANCE_OK`。仅允许写这一文件，不允许外部网络/额外操作。
- `status` 按序进入 `queued → running → completed`，`started_at=2026-10-17T19:15:56Z`、`finished_at=2026-10-17T19:16:13Z`，无错误。
- Bridge `GET /tasks/{task_id}/artifacts` 返回一个文件；本机确实存在 `dsh-worker-acceptance.md`，大小 50 字节，有标题和成功标记，不含 `AI_TEAM_ARTIFACT_STATUS: in_progress` 占位符；工作区只存在这一产物。
- 该任务的本机 DSH 会话包含 5 条 `assistant/message`，它们的 `source.provider` 全部为 `workbuddy-ai`，`source.model` 全部为 `deepseek-v4.1-flash`，`turn/end` 是 `completed`。未发现回退到 `deepseek-account` 的证据；不声称这能证明未来任何任务绝对不会回退。
- 验收后 Bridge 仍 `dsh_available=true`，在线 Worker 数量为 5，没有未完成的 DSH 任务，launchd 进程仍存活。

## 后续工作与风险

1. **可以接单：** AI Team 可把正式任务指定给 `dsh`，但每次模型请求前必须维持国际站费用/账号/模型校验；不允许主动选择收费模型、绕开门禁或自动切换国内站。
2. **费用不等于永远免费：** 当前 `x0.00` 证明查询时的促销条件，不是未来价格保证；监测每次调用的实际来源，并限制任务规模/时限。
3. **恢复策略：** `KeepAlive=false` 表示 Worker 崩溃后不会自动重启，有利于当前费用安全，但需监控在线状态并安全手动恢复。后台周期费用门禁失效时会停止派工。
4. **仅证明最小任务：** 尚未以新 0.7.6 的正式 Radar 开发任务或用户 R3 验收任务做端到端测试，不能用这次小型任务冒充 Radar 已可用。
5. **协作：** Muse 应基于会话来源与实际任务证据交叉核查潜在收费回退路径，继续审查 Radar H2 / 后续 H3；不要不必要地重复模型试探。

## 证据采集方式

本轮在 Mac 本机只读取 `/health`、`/agents`、`/tasks/{id}`、`/artifacts`、私有 SQLite 工作者心跳和 DSH 本地压缩会话中的**来源字段**。没有公开凭据、登录 token、私有对话正文或敏感本机目录内容。
