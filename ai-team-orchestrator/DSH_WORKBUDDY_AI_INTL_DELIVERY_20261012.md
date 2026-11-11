# DSH Worker 国际站免费模型接入交付

日期：2026-10-12。火山引擎保持冻结，桌面版插件和登录不受影响。

## 部署

- DSH 服务：ai.team.dsh，Bridge 统一接单与心跳。
- DSH 核心：0.2.0-rc.2（用户目录 .local/state/ai-team/dsh-intl-runtime）。
- 国际站独立 Profile：~/.dsh/profiles/ai-team-intl。
- 插件版本：dsh-workbuddy-connect 0.7.2。
- 默认 Provider：workbuddy-ai，模型：deepseek-v4.1-flash，禁止 -sg 收费变体。
- 插件沿用国际版 WorkBuddy AI App 登录：www.workbuddy.ai、signed-in、source=desktop。
- 原旧版 Headless Profile 已恢复原始配置；DSH Desktop 插件 0.7.2 未修改。

## 兼容修复

- 原因：Headless Agent 创建速度快于国际版插件异步目录加载，导致 UNKNOWN_MODEL。
- 修复：仅在隔离安装的 Headless Runner 创建 Agent 前等待目标模型可用，最多 25 秒。
- 兼容补丁在 dsh-node-worker/scripts/patch-isolated-headless-readiness.mjs。
- 备份原版隔离模块，并在 DSH 服务启动时幂等重放补丁；内核版本变化即停止补丁，需重新审核。

## 免费门禁

- 代码：dsh-node-worker/src/international-free-guard.js。
- 每次模型调用前检查国际站登录、插件/内核版本、实际 DSH 默认 Provider 与模型。
- 仅接受国际版 App 真实目录，缓存时间不得超过 24 小时。
- 目标模型必须标记 billing.free=true、credits=x0.00，并有当期倍率 0 的有效促销。
- 距免费促销截止至少 10 分钟；非免费、无法确认、已过期全部拒绝接单。
- 运行期间每 10 分钟复查，失败时停止 Worker，向 Bridge 标注不可用。
- 不自动回退原生 DSH、中国站 CodeBuddy CLI 或任何可能收费的模型。
- 价格来自国际版 App 的最新快照，不能保证服务商未来永久免费。
- 当前国际站免费促销快照显示截至 2026-11-01 00:00 北京时间。

## 实测

- 国际站 Provider、账号登录、x0.00 免费资格检查：PASS。
- DSH 真实模型预检：准确返回 DSH_MODEL_OK。
- Bridge 真实任务：task_0f44465546e2b36278ff，completed。
- 隔离文件：.orchestrator/worker-acceptance/dsh-international-workbuddy-ai-20261012/international-free-proof.md。
- 文件包含 AI_TEAM_ARTIFACT_STATUS: completed 与 DSH_WORKBUDDY_AI_FREE_ROUTE_OK。
- Bridge 实际进度事件：international_free_route。
- Bridge 五个 Worker 在线：DSH、Qwen、MiMo、WorkBuddy、OpenCode。

## 回滚与注意

- Mac 启动脚本：~/.config/ai-team/run-dsh-worker.sh。
- 切换前已备份：run-dsh-worker.sh.before-international-plugin-20261012.bak。
- 本次只修改 DSH 的后台模型路由；独立 WorkBuddy Worker 仍使用自己的登录状态。
- 不使用火山引擎，不修改 Radar 生产数据库和其他 Worker 配置。
