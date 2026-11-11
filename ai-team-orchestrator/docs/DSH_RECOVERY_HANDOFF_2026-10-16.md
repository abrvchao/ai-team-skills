# DSH Worker 故障根因与安全恢复交接｜2026-10-16 22:03 CST

> 本文件面向 Muse / 其他 Agent 的离线技术排障；**未恢复 DSH、未调用模型、未刷新第三方 API**。请不要把旧价格缓存视为实时免费承诺。

## 1. 已复核证据（Mac 本机只读）

| 证据 | 结果 |
|---|---|
| `http://127.0.0.1:8765/health` | `status=ok`, `online_workers=4`, `dsh_available=false`, `tasks=362` |
| DSH stderr | 持续 `DSH international free model unavailable: international_catalog_stale` |
| 缓存 | `~/.dsh/.workbuddy-ai-catalog.json`，最近 `fetchedAtMs=2026-10-13 12:03:47 CST` |
| 缓存原有模型 | 国际版 `deepseek-v4.1-flash`，历史快照标记 `billing.free=true`, `credits=x0.00`；**当前价格未核实** |
| 插件 / Core | `dsh-workbuddy-connect 0.7.2` / `@deepseek-ai/dsh 0.2.0-rc.2`，安装文件存在 |
| launchd | `ai.team.dsh` 当前服务**未加载**，但 `print-disabled` 显示 `enabled`；plist `RunAtLoad=true`, `KeepAlive.SuccessfulExit=true`, `ThrottleInterval=30` |
| Grok | `ai.team.grok` 处于 `disabled`，不得重新启用 |
| 离线门禁复现 | `checkInternationalCatalog(snapshot)` → `international_catalog_stale` |
| Node 离线测试 | DSH readiness 相关 **15/15 passed**；没有覆盖“过期目录自恢复”场景 |

## 2. 根因：先检查过期目录，后启动负责更新目录的插件

1. `ai-team-orchestrator/dsh-node-worker/src/index.js:374` 预检先调用 `assertInternationalFreeEligible()`，**然后**才会 `runDshPrompt`。
2. `dsh-node-worker/src/international-free-guard.js:86-109` 先从 `~/.dsh/.workbuddy-ai-catalog.json` 读缓存并检查 24 小时新鲜度；旧缓存直接 `international_catalog_stale`，甚至来不及进入其后的账号状态/模型配置检查。
3. `dsh-node-worker/src/readiness.js:92-135` 仅尝试一次，报告 `model_unavailable`；`dsh-node-worker/src/index.js:596` 在无法启动时退出码 1，worker 不注册到 Bridge。
4. `dsh-workbuddy-connect/lib/index.js` 的 `syncAll`/`fetchCatalog` 依赖插件运行时启动。插件在正常运行后才通过 `/v3/config` 刷新目录；而当前 Worker 的安全门禁在插件运行前已拒绝。因此存在**启动—目录刷新死锁**。
5. 此外 launchd 服务目前被 bootout，虽保持 enabled + RunAtLoad；它无法在当前会话中自动恢复，且在下一次登录时存在失败启动风险。

**已排除或尚未确认：** 可以确认 Bridge 活着、两组件安装存在、过期缓存确实是当前首个硬阻塞点；**尚未验证**国际站账号目前是否登录、上游模型是否仍免费、目录刷新网络可用性、插件真实配置、任何一次真实模型推理。不能推断 OpenRouter 的 Excel API Key 能解决 DSH（两者是不同渠道）。

## 3. 另一个源码缺陷（不会解决此次启动阻塞，但必须修）

`dsh-node-worker/src/index.js:566-599` 设立每 10 分钟运行的 `freeWatch`，但在 `startDshFromEnvironment` 返回后无条件执行 `clearInterval(freeWatch)`；即使启动成功，预期的**每 10 分钟主动免费资格检查也被取消**。`runWithTransientPolicy` 每次发任务前仍检查，但长时间在线空闲期间没有持续监控。应只在启动失败/清理时清除定时器，成功启动期间保留并安全处理异常。

## 4. 安全修复方案（先实现无网络单测，再按授权启动）

**不可绕过免费的成本门禁，不可手动更改缓存时间戳。** 建议分为两个明确阶段：

1. **仅目录恢复阶段**：以隔离版 `ai-team-intl` 凭据运行单次只读的国际 WorkBuddy App 模型目录/价格刷新（`/v3/config`），**不发送 prompt**，不得回退国内/SG/其他模型，不做余额探测、推理或重试风暴。此行为仍属于第三方 API 请求，须获得用户明确授权后再执行。刷新失败则标记 `blocked` 并终止，不自动拉起 worker。
2. **资格验真和启用阶段**：对刷新所得目录执行 `checkInternationalCatalog`（来源、账号、模型 ID、真正 `free=true`、`x0.00`、促销窗口充足、时间戳未过期）。再核对国际站账号、有效配置、隔离 profile。全部通过后方可手动恢复 DSH Worker；是否允许发出一次真实免费模型预检，也需遵守用户模型调用授权与费用限制。失败不启动，不回退收费模型。
3. **服务级风险封堵**：在免费资格恢复前禁用登录自动启动（先只读审查/备份 launchd plist；`RunAtLoad=false` 与 disabled 状态要经实际核对），启动失败不重试；worker 注册以后给免费资格定期复查并设置故障停机。
4. **回归**：使用注入的本地假目录与 fake fetcher 测试 stale、zero-price、变价、撤销、错误账号、离线/超时及恢复成功，确保**零模型调用、零外部流量**；再次查看 Bridge `dsh_available`，最终在用户同意的范围内做真实端到端验收。

## 5. 严格禁止

- **不得**启用 Grok、火山、任何未知费用 Worker；不得用付费后备模型；不得为了“让 DSH 上线”假造 `fetchedAtMs` 或强行跳过 guard。
- **不得**把 App 登录凭据、token、`账号管理0909.xlsx`、用户 home 日志、环境中的密钥提交 GitHub。
- 不能把 **HTTP 200**、15 个 readiness 离线测试或 **旧的 `x0.00` 快照**当成“DSH 已恢复”证据。

## 6. Muse 可直接验收的标准

- `DSH` 远端模型目录的**当前**免费资格有明确证据，并在未授权请求时不会自动刷新/推理。
- `ai.team.dsh` 在不可用时不会登录自动启动/循环重试，且配置经检查。
- 账号+profile+模型一致性全通过，Bridge 中 DSH 有真正的 worker 心跳（而不是只看配置）。
- 有一次具备真实价格门禁的**用户授权**只读任务执行证据及产物；否则仅可称“代码预检通过”，不可称恢复。

## 当前结论

**确定的最先硬故障**：国际模型目录缓存过期 + 预检先于刷新，导致冷启动互锁。**最终是否还能使用免费模型未知**，取决于授权刷新后的上游实时返回。此前已复核的 DSH 进程包装器超时/安全修复属于另一个模块，**不能单独解决此次 DSH 不在线的问题**。
