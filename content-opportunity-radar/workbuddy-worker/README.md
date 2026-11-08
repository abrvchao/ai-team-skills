# WorkBuddy Node Worker

CodeBuddy CLI (WorkBuddy 桌面版) 接入 AI Team Orchestrator 的独立 Worker Adapter。

## 快速开始

```bash
# 确保 Bridge 已启动并配置 workbuddy 为 pull agent
python bridge.py --enabled-pull-agents dsh workbuddy

# 启动 WorkBuddy Worker
cd workbuddy-worker
AI_TEAM_BOOTSTRAP_TOKEN=your-token node src/index.js
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CODEBUDDY_BIN` | 自动检测 | CLI 路径 |
| `CODEBUDDY_MODEL` | `auto` | 模型 ID |
| `CODEBUDDY_EFFORT` | - | 推理力度 |
| `CODEBUDDY_TASK_TIMEOUT_MS` | `1800000` | 任务超时 (30min) |
| `CODEBUDDY_MAX_TURNS` | `100` | 最大 turn 数 |
| `WORKBUDDY_USE_WORKTREE` | `0` | 启用 git worktree |
| `AI_TEAM_BRIDGE_URL` | `http://127.0.0.1:8765` | Bridge 地址 |
| `AI_TEAM_BOOTSTRAP_TOKEN` | - | 认证令牌 |

## 测试

```bash
npm test
npm run check
```

## 设计文档

详见 [DESIGN.md](./DESIGN.md)。
