# DSH Worker 免费模型优先路由（2026-10-11）

> **历史版本，已停用。** 当时的 CodeBuddy CLI 登录站点未确认，后续已发现它与国际版桌面登录并不相同。2026-10-12 起正式方案改为隔离 DSH 核心 + dsh-workbuddy-connect + WorkBuddy AI 国际站免费模型门禁。请以 DSH_WORKBUDDY_AI_INTL_DELIVERY_20261012.md 为准，勿恢复本文件描述的旧路由。

## 已实现

- 保留 DSH Worker 的 Bridge 派工、进度上报与产物验收。
- macOS 启动脚本指定 DSH_MODEL_BACKEND=workbuddy，使用已经登录的
  CodeBuddy CLI；模型固定为 deepseek-v4.1-flash。
- CodeBuddy 执行模式 acceptEdits；工具限制为 Read、Write、Edit、
  Grep、Glob、Bash，没有启用 dangerously-skip-permissions。
- 预检只允许文本回复，禁用所有工具，必须准确响应 DSH_MODEL_OK。
- 模型失败、会话登出、限流时不自动回退原生 DSH，不自动调用付费 API。
- 进度事件标明 free_model_route，实际文件产物仍须通过 DSH 原有检查。

## 本机实测

- DSH 专项测试：32/32 通过。
- CLI 模型真实预检通过。
- 临时工作区真实文件写入测试通过。
- Bridge 真实派工：task_66261a62505640ce018c 已完成，提交文件
  free-route-e2e.md 包含 DSH_WORKBUDDY_DEEPSEEK_OK，进度包含
  free_model_route，产物就绪标记通过。
- 未启动或调用火山引擎，原 DSH 入口及凭据保持原样。

## 必须明确的限制

- 实证确认的是已登录 CodeBuddy CLI 能调用 DeepSeek v4.1 Flash。
- 本机没有核验账号结算信息，因此不能仅凭调用成功保证零计费。
- 也不能从 CLI 返回值直接确认当前会话登录的是国际站或中国站。
  用户所述“免费”是选择优先通道的依据，不代表永久免费承诺。
- 如未来需要切回原生 DSH，须先确认没有进行中的任务及可能费用。

## 回滚

- 当前启动脚本：用户目录 .config/ai-team/run-dsh-worker.sh。
- 原脚本备份：run-dsh-worker.sh.before-free-deepseek-20261011.bak。
- 仅在用户明确要求时恢复，不做收费后端的自动回退。
