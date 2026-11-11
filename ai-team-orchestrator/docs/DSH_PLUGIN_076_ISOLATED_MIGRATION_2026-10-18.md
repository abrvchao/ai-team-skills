# DSH 国际站插件 0.7.6 隔离升级与安全验收｜2026-10-18

**状态：已在用户 Mac 本机完成隔离版 0.7.2 → 0.7.6 迁移，离线验收通过；AI Team DSH Worker 仍未上线。**

## 为什么更新

- Muse 在桌面版证实 `dsh-workbuddy-connect@0.7.6` 能直接通过 `workbuddy-ai/deepseek-v4.1-flash` 输出，不再因国际版 HTTP 400 触发回退。
- Mac 本机比较插件代码后确认 `0.7.6` 的聊天请求头包含 `X-Conversation-ID`、`X-Conversation-Request-ID`、`X-Conversation-Message-ID`。`0.7.2` 不包含这项修复。
- 原 AI Team `dsh-node-worker/src/international-free-guard.js` 固定要求 `0.7.2`，仅复制新插件会被版本门禁拒绝。本次同步将门禁明确固定为 **0.7.6**，并明确拒绝 `0.7.7`、`0.7.8`。
- **不更新** DSH 隔离内核 `@deepseek-ai/dsh@0.2.0-rc.2`，不触碰桌面版插件、账号凭据和模型目录计费字段。

## Mac 上已执行的操作

1. 检查 `~/.dsh/profiles/desktop/node_modules/dsh-workbuddy-connect/` 是 `0.7.6`，与隔离旧版的 Node 版本要求、`@earendil-works/pi-ai@0.87.1` 依赖和 `0.2.0-rc.2` peerDependencies 相同。
2. 在用户私有目录 `~/.local/state/ai-team/backups/dsh-ai-team-intl-before-076-20261018-025547/` 创建**完整**的旧版插件、`package.json`、`pnpm-lock.yaml` 备份；不提交备份或凭据到 GitHub。
3. 先复制桌面版 `0.7.6` 至隔离 `node_modules` 临时目录，逐一比对 package、主要 lib 文件 SHA-256，再本地原子替换旧 `0.7.2`，只改隔离版。
4. 把隔离 profile `package.json` 依赖固定成 `0.7.6`，并将 `pnpm-lock.yaml` 中对应版本/完整性校验值对照已安装桌面版锁文件更新，避免以后意外装回 `0.7.2`。
5. 修改仓库门禁为 `REQUIRED_PLUGIN_VERSION="0.7.6"`，新增 `test/isolated-plugin-version.test.js`，合成测试证明 0.7.2 / 0.7.7 / 0.7.8 和不匹配内核均会在运行外部 CLI 前被拒绝。

## 独立验收证据

| 验证 | 结果 |
|---|---|
| 安装的隔离插件版本 | `0.7.6` |
| 桌面版插件版本 | `0.7.6`，未修改 |
| 隔离内核版本 | `0.2.0-rc.2`，未修改 |
| 已安装隔离插件 ES module 导入 | PASS |
| 已安装插件中三个 `X-Conversation-*` 头 | 静态检查全部存在 |
| `assertInternationalFreeEligible()` 真实隔离版完整检查 | **PASS**：登录身份、唯一国际插件、准确的 `workbuddy-ai/deepseek-v4.1-flash` 生效配置、最新本机价格目录及有效零倍率促销 |
| `node --test dsh-node-worker/test/*.test.js` | **55/55 passed**，纯离线 |
| `ai.team.dsh` LaunchAgent | `disabled`，仍未启动 |
| Grok | `disabled` |
| Bridge `/health` | `status=ok`、`online_workers=4`、`dsh_available=false` |
| 本轮模型请求 | **0**；没有进行 DSH 正式任务或再次调用预检 |

**重要区分：** 完整本地安全门禁通过，不等于模型端到端成功、更不等于 Worker 已完成 Bridge 心跳与交付。Muse 对桌面版 `0.7.6` 的成功记录不能自动替代 **AI Team 隔离版** 的验收。

## 剩余风险与下一步

1. **支付/回退风险：** `--dump-config` 能证明默认模型是国际 `workbuddy-ai/deepseek-v4.1-flash`，同时内核配置仍包含 `deepseek-account` 内置提供商定义。不能仅因它存在便认定发生回退；正式验收需核验每次模型输出的**实际 provider/model**，并保证不存在未经授权的收费后备模型。
2. **真实模型预检尚需单独授权：** 此前单次预检授权已使用完毕。未经用户再次明确批准，不会设置启动自动重试、不加载 DSH Worker 或发起新的 WorkBuddy 模型请求。
3. **版本冻结：** 不升级 `0.7.7/0.7.8`，不安装内核 `0.2.1`。当前隔离内核热补丁与 `0.2.0-rc.2` 绑定，升级前需单独审查。
4. **后续经授权的验收顺序：** 手动单次真实 preflight → 精确 `DSH_MODEL_OK` 且实际响应提供商为 `workbuddy-ai` → 控制 Worker 生命周期注册 → Bridge `dsh_available=true` 和真实心跳 → 单个小型任务与产物复核。任一步失败，停止并保留费用门禁，无回退。

## 回滚方法（仅 Mac 本机运维者）

备份目录：`$HOME/.local/state/ai-team/backups/dsh-ai-team-intl-before-076-20261018-025547`。先确认 `ai.team.dsh` 已停用，再将其中的 `dsh-workbuddy-connect/`、`package.json` 和 `pnpm-lock.yaml` 复原到 `$HOME/.dsh/profiles/ai-team-intl/` 对应位置，并将仓库门禁恢复到旧版。**回滚后不得绕过版本检查强行启用 Worker**。

## 给 Muse 的任务

在 GitHub 审查本次 `REQUIRED_PLUGIN_VERSION` 变更和三项版本负面测试；特别审查 **实际模型运行时是否可能自动回退** 到 `deepseek-account`（而非仅看默认配置），是否能通过离线配置/测试防止回退。在未获新授权时，不要重新进行模型预检。结论请写入共享信箱 `docs/agent-collab/muse-to-chatgpt.md`。
