# WorkBuddy Node Worker

WorkBuddy 作为 AI Team 的注册式拉取 Worker（registered pull worker），
由 WorkBuddy 官方 CLI（CodeBuddy CLI，`@genie/agent-cli`）驱动。

## 运行前提

1. AI Team Bridge 在跑：`http://127.0.0.1:8765`
2. bootstrap token 存在：`ai-team-orchestrator/.orchestrator/bootstrap-token`
3. **CLI 已完成官方登录**（否则预检必然失败，见 `../WORKBUDDY_HANDOFF_RESULT.md` 第三节）

## 运行

```bash
bash scripts/start.sh     # 启动（后台）
bash scripts/status.sh    # 状态
bash scripts/stop.sh      # 停止
bash scripts/restore.sh   # 重启机器 / Bridge 重启后恢复
```

## 测试

```bash
node --test          # 37 条单元测试
node --check src/index.js
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WORKBUDDY_CLI` | 桌面版内置 CLI 绝对路径 | CLI 入口 |
| `WORKBUDDY_NODE_BIN` | 当前进程的 node | 用来跑 CLI 的 node |
| `AI_TEAM_BRIDGE_URL` | `http://127.0.0.1:8765` | Bridge 地址 |
| `AI_TEAM_BOOTSTRAP_TOKEN_FILE` | `../.orchestrator/bootstrap-token` | 就绪上报凭据（路径，非内容） |
| `WORKBUDDY_MODEL` | `deepseek-v4.1-flash` | 默认模型 |
| `WORKBUDDY_PERMISSION_MODE` | `plan`（只读） | CLI 权限模式 |
| `WORKBUDDY_MAX_TURNS` | `30` | 单任务最大轮次（成本闸门） |
| `WORKBUDDY_TASK_TIMEOUT_MS` | `1800000`（30 分钟） | 单任务墙钟超时 |
| `WORKBUDDY_ALLOW_PAID_MODELS` | 未设置 | 设为 `1` 才允许付费模型 |
| `WORKBUDDY_ALLOW_ANY_MODEL` | 未设置 | 设为 `1` 才允许清单外模型 |

## 安全设计

- **默认只读**：`--permission-mode plan`，不放开本机写操作；
- **产物由适配器落盘**：不依赖模型自己调用写文件工具，路径受工作区约束（`safeJoin` 防 `../` 穿越）；
- **付费闸门**：`minimax-m3-pay` 默认拦截；
- **成本闸门**：`--max-turns` + 墙钟超时，到点 SIGTERM，5 秒后 SIGKILL；
- **凭据不落日志**：token 只以文件路径形式传递；
- **用量诚实**：CLI 不报用量时标记 `usage_unknown_by_cli`，不假造数字。

## 已知坑

**★ 必须剔除 `SERVER__PORT` / `SERVER__HOST`**：WorkBuddy 桌面版会让子进程继承
`SERVER__PORT=<桌面版端口>`，CLI 照此端口 `listen` 会撞车 → `EADDRINUSE` → **主流程永久挂起**。
Worker 已在 `buildCliEnv()` 中统一剔除，并有单元测试保护。

## 目录结构

```
workbuddy-node-worker/
├── src/
│   ├── index.js        # Worker 主实现（CLI 运行器 / 任务处理器 / 模型闸门）
│   └── readiness.js    # 就绪与预检（认证失败不重试）
├── test/
│   └── workbuddy.test.js
├── scripts/
│   ├── start.sh  stop.sh  status.sh  restore.sh
└── .run/               # 运行时 pid 与日志（不应提交）
```
