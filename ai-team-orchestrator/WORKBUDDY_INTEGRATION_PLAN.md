# WorkBuddy 接入方案（Integration Plan）

> 项目：AI Team Orchestrator × WorkBuddy
> 日期：2026-10-11
> 结论摘要：**技术路线已选定并落地；唯一阻塞为 CLI 认证，需要一次官方登录。**

---

## 一、候选接入方式实测比较

任务书要求先确认 WorkBuddy 支持哪些官方接入接口。以下是逐个实测的结果（全部基于本机 v2.156.0）。

| # | 候选方式 | 实测结论 | 可用性 |
|---|---|---|---|
| 1 | **WorkBuddy CLI / CodeBuddy CLI**（`codebuddy`，`@genie/agent-cli`） | 二进制存在、`--print` 非交互可用、`--output-format stream-json` 可解析；**独立运行时认证失败** | ⚠️ 待认证 |
| 2 | **ACP（Agent Client Protocol）** | 官方支持：`--acp`、`--acp-transport stdio\|streamable-http`；认证缺失时通过 `_codebuddy.ai/authUrl` 通知下发登录链接 | ⚠️ 待认证 |
| 3 | **MCP（Model Context Protocol）** | CLI 是 MCP **客户端**（`--mcp-config`、`codebuddy mcp`）；WorkBuddy 不做 MCP server，方向不匹配 | ❌ 不适合本需求 |
| 4 | **桌面版本地接口** | 实测存在 CodeBuddy Remote Control 服务（`127.0.0.1:<动态端口>`，`Authorization: Bearer <网关密码>` 可通）；但**端口每次启动变化**、内部路由未文档化 | ⚠️ 备选 |
| 5 | **其他自动化方式** | `codebuddy daemon`（守护进程）、`--serve`（HTTP + REST + ACP over SSE）、`--bg`（后台会话）、`--remote-control`（AgentOS）、`--swarm` | ⚠️ 待认证 |

### 关键实测数据

```
CLI 版本                       : 2.156.0
CLI 路径                       : /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
非交互调用                      : -p/--print 支持
输出格式                       : text | json | stream-json
权限模式                       : acceptEdits | bypassPermissions | default | plan | dontAsk | auto
ACP 传输                       : stdio | streamable-http
依赖中已含 A2A 协议              : @a2a-js/sdk
```

---

## 二、选型结论

**选定路线：注册式拉取 Worker（registered pull worker）+ WorkBuddy CLI 非交互调用。**

理由：

1. **与现有架构一致**。AI Team 已在用 `AgentWorker`（register → heartbeat → lease → events）的 pull 模式，DSH / Qwen / MiMo 都是这么接的。复用 SDK 比自创协议风险小得多。
2. **满足「无人值守」**。Worker 常驻后，Supervisor 派工不需要人工复制粘贴。
3. **满足安全边界**。Worker 通过 `--permission-mode plan`（默认只读）限制 CLI 本机写操作；产物由 Worker 适配器落盘，路径受工作区约束。
4. **可切换**。若未来 ACP 认证打通，只需替换 `runWorkBuddyPrompt` 这一处实现，任务处理逻辑不变。

被否决的路线：

- **MCP**：WorkBuddy 侧是 MCP 客户端，让 CLI 变成「被调用的 server」不在官方支持面内。
- **桌面版 Remote Control 直接对接**：实测可用但不是稳定契约 —— 服务端口随桌面版每次启动变化（本次观测到 62721 → 65339），且 `/internal/*` 路由未文档化。作为**备选**保留，不作为主路线。

---

## 三、架构

```
Supervisor / ChatGPT
        ↓ MCP
Agent Bridge (127.0.0.1:8765)
        ↓ queued task
workbuddy-node-worker  ← register / heartbeat / lease / events
        ↓ spawn（剔除 SERVER__PORT）
WorkBuddy CLI (codebuddy --print --output-format stream-json)
        ↓
WorkBuddy 模型（deepseek / glm / kimi / minimax / step / hy ...）
```

---

## 四、关键工程发现（决定了实现细节）

### 4.1 CLI 卡死根因与修复 ★

WorkBuddy 桌面版会让子进程继承 `SERVER__PORT=<桌面版自己的端口>`。CLI 启动时会**照这个端口去 listen**，撞上桌面版 → `EADDRINUSE` → 内部 Promise 未捕获拒绝 → **主流程永久挂起（实测 >7 分钟无输出）**。

```js
// CLI 源码：端口优先级
if (opts.port) process.env.SERVER__PORT = String(opts.port);
else if (void 0 === process.env.SERVER__PORT) { /* 自动挑一个稳定端口 */ }
```

**处理**：Worker 在 spawn 时统一剔除 `SERVER__PORT` / `SERVER__HOST`（见 `buildCliEnv()`，并有单元测试覆盖）。

### 4.2 认证机制（当前唯一阻塞）★

CLI 的凭据由桌面版通过 **sidecar 凭据引导通道**在 spawn 那一刻注入：

```
CODEBUDDY_SIDECAR_READY_SOCKET
CODEBUDDY_SIDECAR_READY_TOKEN
CODEBUDDY_SIDECAR_READY_SESSION_ID
CODEBUDDY_SIDECAR_CREDENTIAL_BOOTSTRAP_SOCKET
```

这些变量**只存在于桌面版的子进程**中。独立启动的 CLI 拿不到，spill 配置
（`acc-product-config-v3.json`）里的 `authentication.type = cli-external-link`，**不含 token**。

→ 独立运行的 CLI 必然报 `Authentication required. Please use /login command`。
**这不是 bug，是 WorkBuddy 的架构设计。**

**解法见 `WORKBUDDY_HANDOFF_RESULT.md` 第三节。**

### 4.3 环境干扰

环境中存在 `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy`（本地代理），会让 CLI 的**子命令**
（`ps` / `daemon` / `config` 等）抛 `TypeError: Right-hand side of 'instanceof' is not callable`。
`--print` 主流程不受影响。

---

## 五、实现清单

| 要求（任务书四） | 实现位置 | 状态 |
|---|---|---|
| 1. Worker 注册与心跳 | `src/index.js` → `AgentWorker` | ✅ |
| 2. 真实模型调用预检 | `src/readiness.js` → `runModelPreflight` | ✅ |
| 3. Supervisor 领取任务 | `AgentWorker.lease`（pull 模式） | ✅ |
| 4. 工作区隔离 | `resolve` + `safeJoin`（含路径穿越防护） | ✅ |
| 5. 模型选择与执行 | `resolveModel` + 允许清单 + 付费闸门 | ✅ |
| 6. 阶段进度回传 | `workbuddyProgressFromEvent` → Progress Event | ✅ |
| 7. 产物写入与登记 | `initializeRequiredArtifacts` / `persistFinalText` / `collectArtifacts` | ✅ |
| 8. 取消与超时 | AbortSignal + SIGTERM→SIGKILL 双段终止 | ✅ |
| 9. 错误分类 | `classifyCliError`（auth/rate_limit/timeout/cancelled/cli_missing/...） | ✅ |
| 10. 用量记录 | `extractUsage`（拿不到时显式 `usage_unknown_by_cli`） | ✅ |

### 成本与安全

| 要求 | 落实 |
|---|---|
| 默认不启用付费模型自动执行 | 默认模型 `deepseek-v4.1-flash`；`minimax-m3-pay` 需 `WORKBUDDY_ALLOW_PAID_MODELS=1` |
| 单任务调用次数 / 时长限制 | `--max-turns`（默认 30）+ 墙钟超时（默认 30 分钟） |
| 认证失败不得无限重试 | `isRetryableReadinessError()`：auth 立即返回 |
| 不泄露密钥 | 凭据只走「文件路径」，代码/日志/产物不打印环境变量 |
| 不改 DSH/Qwen/MiMo 配置 | 未触碰 |
| 不自动启用 Grok/Gemini/OpenRouter | 未触碰 |
| 不动 Cloudflare Access / 公网配置 | 未触碰 |
| 不提交凭据 | 无凭据写入仓库 |

---

## 六、遗留与下一步

1. **CLI 认证**（阻塞，需用户操作）。
2. **Bridge 需要重启并加入 workbuddy**：运行中的 Bridge（pid 22509）加载的是旧版 `registry.py`，
   `/agents` 里看不到 workbuddy；且启动脚本 `~/.config/ai-team/run-bridge.sh` 的 `--pull-agent`
   列表里没有 workbuddy。两项都属生产配置变更，**待用户确认后执行**。
3. 认证与 Bridge 就绪后，跑验收测试 A–E（见 `WORKBUDDY_INTEGRATION_TEST.md`）。
