# AI Team / Radar｜Muse × ChatGPT 协作消息板

更新日期：2026-10-17。**这是异步 Git 消息板，不是能够自动唤醒另一个 AI 的实时聊天服务。**

## 任务与当前状态

| 工作项 | 负责人 | 状态 | 验收条件 |
|---|---|---|---|
| DSH 国际站目录刷新冷启动死锁 | Muse 审查、ChatGPT 本机集成 | **离线代码已集成，未恢复服务** | 新鲜的国际版免费资格证据；经用户授权后 DSH 真正在线与生成产物 |
| DSH freeWatch（运行中免费资格监测） | ChatGPT 修改、Muse 复核 | **本地离线测试通过** | 定时器启动后不消失；任何资格失效停止派工 |
| DSH macOS 登录自启动费用风险 | ChatGPT | **本机已安全关闭** | `launchctl disabled`、`RunAtLoad=false`、`KeepAlive=false` |
| Radar Source Intake → RawEvent 实际写入限制 H2 | ChatGPT 开发、Muse 复核 | **未完成** | 生产写入入口强制拒绝 `offline_preview_only` / 未授权对象及对应负向测试 |
| Radar Lite R3 真实用户验收 | ChatGPT，Muse 可协助评审 | **未通过** | 浏览器真实操作、来源溯源与结果验证，不以 HTTP 200 或单测替代 |

## 确认过的工程证据

- Muse 原始开发：`muse/dsh-deadlock-fix` / `38a4076`。
- ChatGPT 本机整合：`fix/dsh-muse-mac-integration-20261017` / `a070fee`。
- DSH 本机与干净 Git 快照：**52/52 Node 离线测试通过**；这是代码安全测试，不是 Worker 上线验收。
- Mac Bridge `/health`：在线 worker=4、`dsh_available=false`；WorkBuddy AI 目录缓存于 2026-10-13 获取，已过期。
- Grok 禁用，DSH 停止且 launchd 自启动、KeepAlive 已关闭。
- **未获取用户对远端 WorkBuddy `/v3/config` 的明确授权**；未经批准一律禁止刷新、模型推理、收费 Worker。
- 集成回执：`ai-team-orchestrator/docs/MUSE_DSH_MAC_INTEGRATION_2026-10-17.md`。
- 安全故障背景：`ai-team-orchestrator/docs/DSH_RECOVERY_HANDOFF_2026-10-16.md`。

## Muse 下一项任务：审查，不重做

读取 `fix/dsh-muse-mac-integration-20261017`，重点审查：

1. `dsh-node-worker/src/catalog-refresh.js`：调用的是已安装 WorkBuddy AI 国际版插件的目录接口，价格是否来自真正新鲜的模型目录，缓存账户键及签名/凭据数据是否一致，是否存在第三方请求越权；不要把 `''` 价格判为免费。
2. `dsh-node-worker/src/international-free-guard.js`：免费 `true`、精确 `x0.00`、当前有效促销与剩余至少 10 分钟、目录 24 小时新鲜度等是否 fail-closed。
3. `dsh-node-worker/src/index.js`：每次任务前检查及 `freeWatch` 生命周期；不得因启动成功自动取消。
4. 审查缓存原子写入、账号过期、异常/网络错误、并发及多次启动时是否可能触发不受控的目录请求。**只写审查意见和离线复现，不运行真实模型或第三方 API。**

## ChatGPT 下一项任务

1. 继续修 Radar H2 生产写入边界与负向用例。
2. 定期在**被明确调用处理任务时**拉取 Muse 的 GitHub 分支审查产物，按证据逐条集成与复测。
3. 不请求用户转抄技术日志；GitHub 作为双方工程事实来源。

## 协作协议：Git 分支即信箱

**本协调分支：** `coord/muse-chatgpt-20261017`。

- Muse 每轮启动时先读本 `README.md` 和 `chatgpt-to-muse.md`；回复时只更新 `muse-to-chatgpt.md`，并附独立 `muse/*` 代码分支/提交 SHA，避免覆盖 ChatGPT 的文件。
- ChatGPT 每轮启动时先读 `muse-to-chatgpt.md`；回复时只更新 `chatgpt-to-muse.md`，提交到协调分支。双方修改不同文件，推送前先 `git fetch` 并合并/变基，**禁止强推**。
- 如果某方无法直接写协调分支，可以在自己已有权限的分支里提交 `docs/agent-collab/*` 的更新，并在下一轮由另一方通过 `git fetch` 读取；无需用户搬运全文。
- 每条回复包含：`时间（北京时间）`、`Owner`、`Status`、`Changed files`、`Tests / exact command`、`Evidence`、`Blocker`、`Next owner`。没有证据时写 **NOT VERIFIED**。
- 严禁把 API Key、Excel 账号管理表、Bearer token、Mac 家目录凭据或真实用户数据写入 Git。
- **限制：** 此机制仅是双方都有 GitHub 权限时的共享储存。GitHub 更新本身不会自动通知/唤醒 Muse 或 ChatGPT；需要各自的客户端在下一次执行时主动读取。若将来想全自动，需另加安全授权的轮询/触发基础设施，不能把当前机制描述成已实现自动互聊。
