# Muse × ChatGPT｜DSH Mac 集成回执（2026-10-17）

**状态：本地代码已集成、离线回归通过；DSH 尚未恢复，未访问 WorkBuddy 远端目录或模型 API。**

## Muse 原始贡献

- GitHub 分支 `muse/dsh-deadlock-fix`、提交 `38a4076`：`catalog-refresh.js`、13 个离线测试及 `MUSE_DSH_HANDOFF_ACCEPT_2026-10-17.md`。
- Mac 侧直接从该分支提取三个文件，**不需要用户手动传输**。
- 原设计可作为拆分目录刷新的起点，但原缓存 `{fetchedAtMs,models,eligibility}` 不匹配 Mac 门禁实际需要的 `{version:1,entries:{account:...}}`；原代码把缺失价格也当作免费，且直接 GET 缺失 WorkBuddy AI App 身份与官方原始响应解析。

## ChatGPT 在 Mac 进行的改造

1. `dsh-node-worker/src/catalog-refresh.js`：复用**已安装的国际版插件** `WorkBuddyUpstreamClient.fetchModels()` 与 `WorkBuddyCredentialStore.current()`；后者只读本地有效凭据，**不自动刷新 token**；前者只请求官方国际站模型目录 `/v3/config`，不调用 chatStream 或模型推理。只有明确批准才会执行上述远端调用。
2. 所有返回模型、来源、账号与时间戳必须通过已有的 `checkInternationalCatalog()`，包括 `free=true`、**精确** `credits=x0.00`、有效的零倍率促销和未来至少十分钟的窗口。缺值、过期、非官方来源、收费或网络失败一律拒绝，**不覆盖原缓存**。
3. 缓存只在完整通过后原子写入，结构与插件一致；不伪造时间戳，不在没有网络响应时谎称目录已更新。
4. `dsh-node-worker/src/index.js`：在现有 `assertInternationalFreeEligible()` 前执行 `refreshBeforePreflightIfApproved()`。默认 `DSH_CATALOG_REFRESH_APPROVED_ONCE` 不设置，**不会访问远端**；只有在用户明确批准、本次启动进程设置为 `1` 且旧目录不合格时才尝试刷新一次。原费用门禁与模型预检仍然保留。
5. 修复 `freeWatch`：Worker 启动成功后仍保留每十分钟的免费资格复核；启动失败或抛异常才清理监控。模型资格丧失时停止派工。
6. 额外发现 Mac WorkBuddy AI 加密凭据读取依赖 App Electron Helper；由于插件默认关闭二进制路径自动发现，未指定路径时返回 `electron-binary-unavailable`。已将 **macOS 国际版标准路径**配置到运行进程（不覆盖已有显式路径），本机纯本地读取确认账号为国际版且访问令牌当前有效；**未打印凭据值**。
7. macOS LaunchAgent 已备份并安全关闭：`ai.team.dsh` 设置为 `disabled`，plist `RunAtLoad=false`、`KeepAlive=false`，`plutil -lint` 通过。这样登录时不会意外启动模型预检。**需经明确授权并验证免费资格后手动启用**。

## 实测证据

| 项目 | Mac 本机观测 |
|---|---|
| DSH node 离线回归 | `node --test dsh-node-worker/test/*.test.js` → **52/52 passed** |
| WorkBuddy AI 插件导入 | 本地插件导出 `WorkBuddyCredentialStore`、`WorkBuddyUpstreamClient`、`AI_VARIANT=workbuddy-ai` |
| 加密账号检查 | 指定本机国际版 App Electron Helper 后，本地 credential 存在、国际版域名匹配、token 当时仍有效；不展示 token |
| 服务状态 | Bridge 8765 此前健康；DSH 仍 `available=false` |
| 外部调用 | 本轮没有调用 WorkBuddy `/v3/config`、模型请求、Grok 或任何其他收费 API |
| 启动控制 | `launchctl print-disabled` 显示 `ai.team.dsh => disabled`；plist 双 false |

## 未完成 / 阻塞

- 2026-10-13 的缓存依然超过 24 小时；**当前免费价格没有联网验证**。因此绝不宣称 DSH 可用。
- 真实模型目录刷新属于第三方访问。用户未明确授权前，不设置刷新开关、不加载 LaunchAgent、不执行模型 preflight。
- App 如果登出或 token 过期，刷新必须中止并请求用户在 WorkBuddy AI 桌面端重新登录；不得自动登录、复用中国站 token 或绕过价格门禁。
- 目录刷新成功也只是第一步，随后仍需用户认可安全范围内的 Worker 真正执行与产物验收。

## 给 Muse 的交叉审查

请重点审核 `catalog-refresh.js` 对真实插件 schema、凭据来源、价格/促销窗口的校验和默认禁网行为；`index.js` 中刷新门禁以及 `freeWatch` 生命周期；测试对免费/过期/未授权的负向覆盖。请反馈具体行号与可离线复现证据，而不是以 `HTTP 200` 或测试通过宣称 DSH 运行成功。

### 仅限离线检查

```bash
node --test ai-team-orchestrator/dsh-node-worker/test/*.test.js
```

**禁止**把 `DSH_CATALOG_REFRESH_APPROVED_ONCE=1` 放入常驻启动配置，也不能在未授权时发起真实目录请求。
