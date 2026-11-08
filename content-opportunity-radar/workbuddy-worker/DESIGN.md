# WorkBuddy Worker Adapter 设计方案

> 目标：为桌面版 WorkBuddy/CodeBuddy CLI 设计接入 AI Team Supervisor 的最小 Worker Adapter。
> 约束：不修改共享源码（`node-worker/`、`bridge.py`、`team_supervisor.py` 等），所有产物独立。

## 1. 现状分析

### 1.1 CodeBuddy CLI 能力（已验证）

```
路径: /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
关键参数:
  -p, --print                     非交互模式，输出后退出
  --output-format <format>        text | json | stream-json
  -w, --worktree [name]           创建 git worktree 并在其中启动会话
  --worktree-branch <branch>      worktree 基准分支
  --permission-mode <mode>        acceptEdits | bypassPermissions | auto | plan | dontAsk | default
  --max-turns <number>            限制 agentic turn 数
  --tools <value>                 限制工具集（""禁用全部，"default"全部，逗号分隔）
  --disallowedTools <tools...>    禁止特定工具
  --model <model>                 指定模型
  --dangerously-skip-permissions  跳过权限提示（HIGH/CRITICAL 仍询问）
  --system-prompt <prompt>        覆盖系统提示词
  --append-system-prompt <prompt> 追加系统提示词
  --no-session-persistence        禁用会话持久化
  --effort <level>                推理力度
```

### 1.2 现有 Node Worker SDK 模式

所有现有 Worker（qwen、gemini、grok、dsh、doubao、muse）共享同一架构：

```
AgentWorker (node-worker/src/worker.js)
  ├── BridgeClient → Bridge HTTP API (register/lease/heartbeat/event)
  ├── onTask(ctx) → 任务处理函数
  │   ├── promptFor(task, messages) → 构造提示词
  │   ├── runXxxPrompt({ task, prompt, signal, ... }) → spawn CLI 子进程
  │   ├── collectArtifacts(task) → 验证并收集产物
  │   └── finalizePromptFor(task, messages) → 收尾提示词
  ├── createWorker() → 实例化 AgentWorker
  └── startXxxFromEnvironment() → 完整启动流程（preflight + register + start）
```

关键不变量：
- **Truth in Bridge**: 任务状态由 Bridge 管理，Worker 只发事件
- **Artifact Truth**: `assertArtifactReady()` 检查 `in_progress` 标记
- **Finalization**: Supervisor 发 converge/finalize 消息 → Worker 中断探索 → 纯文本收尾
- **Process Isolation**: 每个任务 spawn 独立 CLI 子进程，cwd 设为 `task.workspace`

## 2. 最小适配器设计

### 2.1 架构定位

```
                    ┌─────────────────────────┐
                    │   AI Team Supervisor     │
                    │   (team_supervisor.py)   │
                    └──────────┬──────────────┘
                               │ Bridge HTTP API
                    ┌──────────▼──────────────┐
                    │   Bridge (bridge.py)     │
                    │   127.0.0.1:8765        │
                    └──────────┬──────────────┘
                               │ register/lease/heartbeat/event
                    ┌──────────▼──────────────┐
                    │  workbuddy-node-worker   │
                    │  (本适配器，独立目录)      │
                    │                          │
                    │  AgentWorker(agentId=     │
                    │    "workbuddy")          │
                    └──────────┬──────────────┘
                               │ spawn
                    ┌──────────▼──────────────┐
                    │  codebuddy CLI           │
                    │  -p --output-format      │
                    │  stream-json             │
                    │  --worktree              │
                    │  --permission-mode auto  │
                    └─────────────────────────┘
```

### 2.2 为什么不修改共享源码

1. **注册扩展性**: `registry.py` 的 `AgentRegistry.default()` 使用硬编码列表。新 Worker 通过 `pull_workers` 配置动态注册，不需要修改 registry。
2. **Transport 兼容**: WorkBuddy 使用与其他 pull worker 相同的 Bridge HTTP 协议，无需新 transport。
3. **SDK 复用**: `node-worker/src/` 的 `AgentWorker`、`BridgeClient`、`assertArtifactReady` 直接 import，无需复制。

### 2.3 目录结构

```
workbuddy-worker/
├── DESIGN.md              ← 本文件
├── README.md              ← 使用说明
├── package.json           ← 独立包定义
├── src/
│   ├── index.js           ← 主适配器（handleTask, createWorker, runCodebuddyPrompt）
│   └── readiness.js       ← 就绪检查与启动流程
└── test/
    └── workbuddy.test.js  ← 单元测试
```

## 3. 核心设计决策

### 3.1 CLI 调用方式

```javascript
// 最小参数集
function buildCodebuddyArgs({ prompt, workspace, outputFormat = "stream-json" }) {
  const args = [
    "-p",                                    // 非交互模式
    "--output-format", outputFormat,         // stream-json 用于实时事件流
    "--permission-mode", "auto",             // 自动权限（与 Qwen 的 --approval-mode auto 对齐）
    "--no-session-persistence",              // 不持久化会话，避免磁盘膨胀
    "--max-turns", resolveMaxTurns(),        // 限制 turn 数防止无限循环
  ];
  
  // 模型选择
  const model = resolveCodebuddyModel();
  if (model && model !== "auto") args.push("--model", model);
  
  // 推理力度
  const effort = resolveCodebuddyEffort();
  if (effort) args.push("--effort", effort);
  
  // 工具限制（收尾阶段禁用所有工具）
  // 注意：codebuddy 使用 --disallowedTools 而非 --exclude-tools
  
  // 提示词必须放在最后（positional argument）
  args.push(prompt);
  return args;
}
```

**与 Qwen/Grok 的差异**:
- CodeBuddy 使用 positional prompt（最后一个参数），而非 `--prompt`
- CodeBuddy 的 `--permission-mode` 对应 Qwen 的 `--approval-mode`
- CodeBuddy 没有 `--bare` 模式，需要 `--no-session-persistence` 替代

### 3.2 独立 Worktree 策略

CodeBuddy CLI 原生支持 `--worktree`，但 AI Team 的 workspace 隔离由 Supervisor 在 `team_supervisor.py` 中通过 `.ai-team-runs/<run_id>/primary/<agent_id>/` 目录完成。

**设计决策**: Adapter 层面不主动使用 `--worktree`，原因：
1. Supervisor 已经为每个 agent 创建了隔离 workspace 目录
2. `task.workspace` 已经是隔离的（`.ai-team-runs/<run_id>/primary/workbuddy/`）
3. `--worktree` 会创建额外的 git worktree，增加复杂度且与 Supervisor 的 baseline 机制冲突
4. spawn 子进程时 `cwd: task.workspace` 已经提供了目录隔离

**可选启用**: 如果任务明确要求 git worktree 隔离（如并行实现分支），可通过环境变量 `WORKBUDDY_USE_WORKTREE=1` 启用：

```javascript
if (process.env.WORKBUDDY_USE_WORKTREE === "1") {
  const branchName = `ai-team-${task.task_id}`;
  args.push("--worktree", branchName);
  args.push("--worktree-branch", process.env.WORKBUDDY_WORKTREE_BRANCH || "main");
}
```

### 3.3 超时控制

```
层级超时机制（与现有 Worker 一致）:

1. 进程级超时: CODEBUDDY_TASK_TIMEOUT_MS (默认 30min)
   → setTimeout → child.kill("SIGTERM") → 强制 SIGKILL

2. 任务预算超时: task.metadata.execution_budget.exploration.timeout_seconds
   → 覆盖进程级超时

3. 收尾超时: CODEBUDDY_FINALIZE_TIMEOUT_MS (默认 120s)
   → Finalization 阶段独立超时

4. Turn 限制: --max-turns (默认 100)
   → CLI 层面限制 agentic turn 数，防止无限工具调用循环
```

**进程树终止**（参考 Gemini Worker）:
```javascript
const ownsProcessGroup = process.platform !== "win32";
// detached: true 创建新进程组
// 超时时 process.kill(-child.pid, "SIGTERM") 终止整棵进程树
```

### 3.4 权限安全

```javascript
// 安全策略层级:
// 1. --permission-mode auto: 自动批准常规操作，HIGH/CRITICAL 仍需确认
// 2. --disallowedTools: 禁止危险工具
// 3. 环境变量隔离: API key 通过 env 传递，不出现在 argv

const DANGEROUS_TOOLS_BLOCKED = [
  // 收尾阶段由 Adapter 层控制，不在 CLI 层禁止
  // 因为 CodeBuddy 的 --disallowedTools 语法待验证
];

// 关键安全约束:
// - 不使用 --dangerously-skip-permissions（HIGH/CRITICAL 仍需确认）
// - API key 通过环境变量传递，不嵌入命令行
// - cwd 限制在 task.workspace 内（由 Supervisor 保证）
// - NO_COLOR=1 避免 ANSI 转义序列污染 JSON 输出
```

### 3.5 任务 Artifact 回传

完全复用现有模式：

```javascript
async function collectArtifacts(task) {
  // 1. 验证 required artifacts 存在且不是 in_progress checkpoint
  for (const relativePath of requiredArtifacts(task)) {
    await access(`${task.workspace}/${relativePath}`);
    await assertArtifactReady(task.workspace, relativePath);
  }
  // 2. 收集所有 expected artifacts（required + prompt 中提及的）
  const artifacts = [];
  for (const relativePath of expectedArtifacts(task)) {
    try { await access(`${task.workspace}/${relativePath}`); }
    catch { continue; }
    artifacts.push({ path: relativePath, kind: "file", label: relativePath });
  }
  return artifacts;
}
```

**初始化 checkpoint**: 与 Qwen/Gemini 一致，在模型开始工作前创建 `AI_TEAM_ARTIFACT_STATUS: in_progress` 骨架文件。

### 3.6 stream-json 事件处理

CodeBuddy 的 `--output-format stream-json` 输出 NDJSON 流。事件格式待实际验证，Adapter 采用宽容解析：

```javascript
function codebuddyProgressFromEvent(event) {
  // 尝试多种可能的事件格式
  const kind = String(event?.type || event?.subtype || "").toLowerCase();
  
  if (kind.includes("session") || kind.includes("init") || kind.includes("start")) {
    return { stage: "model_session", message: "CodeBuddy session initialized", current: 2, total: 5 };
  }
  if (kind.includes("tool")) {
    const name = event?.tool_name || event?.name || event?.toolCall?.name || "tool";
    return { stage: "tool_use", message: `CodeBuddy is using ${name}` };
  }
  if (kind.includes("result") || kind.includes("complete") || kind.includes("done")) {
    return { stage: "finalizing", message: "CodeBuddy produced a final result", current: 4, total: 5 };
  }
  return null;
}
```

### 3.7 Finalization（收尾）策略

CodeBuddy CLI 没有 Qwen 那样的 `--exclude-tools` 数组参数来在 CLI 层面移除工具。收尾策略：

1. **首选**: 使用 `--tools ""` 禁用所有工具，配合 `--max-turns 1` 限制为单轮纯文本输出
2. **备选**: 如果 `--tools ""` 不可靠，使用 `--max-turns 0` + `--output-format text` 直接获取纯文本
3. **兜底**: 直接调用 CodeBuddy 底层模型的 HTTP API（如果可获取），与 Qwen 的 `runQwenTextFinalization` 模式一致

当前实现采用方案 1，并在测试中验证行为。

## 4. 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CODEBUDDY_BIN` | 自动检测 | CLI 二进制路径 |
| `CODEBUDDY_MODEL` | `auto` | 模型 ID |
| `CODEBUDDY_EFFORT` | - | 推理力度 (minimal/low/medium/high/xhigh/max) |
| `CODEBUDDY_TASK_TIMEOUT_MS` | `1800000` | 任务超时 (30min) |
| `CODEBUDDY_FINALIZE_TIMEOUT_MS` | `120000` | 收尾超时 (2min) |
| `CODEBUDDY_MAX_TURNS` | `100` | 最大 turn 数 |
| `CODEBUDDY_APPROVAL_MODE` | `auto` | 权限模式 |
| `WORKBUDDY_USE_WORKTREE` | `0` | 是否启用 git worktree |
| `WORKBUDDY_WORKTREE_BRANCH` | `main` | worktree 基准分支 |
| `AI_TEAM_BRIDGE_URL` | `http://127.0.0.1:8765` | Bridge 地址 |
| `AI_TEAM_BOOTSTRAP_TOKEN` | - | Bootstrap 认证令牌 |
| `AI_TEAM_BOOTSTRAP_TOKEN_FILE` | - | Bootstrap 令牌文件路径 |

## 5. 注册与发现

### 5.1 动态注册

WorkBuddy 作为 pull worker 通过 Bridge 的 `/workers/register` 端点动态注册：

```javascript
const worker = new AgentWorker({
  bridgeUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
  agentId: "workbuddy",
  label: "WorkBuddy Code",
  capabilities: ["implementation", "research", "code_review", "testing"],
  onTask: handleTask,
});
```

### 5.2 Supervisor 集成

Supervisor 通过 `TEAM_ORDER` / `IMPLEMENTATION_PREFERENCE` 等列表识别 Worker。
WorkBuddy 当前需要配置为 pull agent 才能被 Dispatcher 路由：

```bash
# 启动 Bridge 时添加 workbuddy 到 enabled pull agents
python bridge.py --enabled-pull-agents dsh workbuddy
```

或在 `team_supervisor.py` 的 `TEAM_ORDER` 中添加 `"workbuddy"`（需修改共享源码，不在本方案范围内）。

**替代方案**: 通过 `ROLE_PROFILES` 映射，将 workbuddy 映射到现有角色（如 `implementation_architect`），但这需要修改 `team_supervisor.py`。

**最小侵入方案**: WorkBuddy 作为独立 pull worker 注册，通过 Bridge 的 lease 机制接收任务。Supervisor 的 `participants` 参数显式指定 `"workbuddy"` 即可。

## 6. 测试策略

### 6.1 单元测试（无外部依赖）

- **参数构建**: 验证 `buildCodebuddyArgs` 输出正确参数
- **安全性**: 验证 API key 不泄露到 argv
- **事件解析**: 验证 `codebuddyProgressFromEvent` 正确映射
- **Artifact 收集**: 验证 checkpoint 初始化和 truth gate
- **超时控制**: 验证超时触发 SIGTERM → SIGKILL
- **Fake CLI**: 使用 mock 脚本模拟 codebuddy CLI 行为

### 6.2 集成测试（需要 CodeBuddy CLI）

- **Preflight**: 验证 CLI 可执行且返回就绪令牌
- **端到端**: 发送简单任务，验证 artifact 产出

## 7. 风险与未知项

| 风险 | 影响 | 缓解 |
|------|------|------|
| `stream-json` 事件格式未完全文档化 | 事件解析可能不完整 | 宽容解析 + 降级为 text 模式 |
| `--tools ""` 在收尾阶段行为不确定 | Finalization 可能仍有工具调用 | 备选方案：`--max-turns 0` |
| CodeBuddy 可能缓存会话状态 | 多任务间状态泄漏 | `--no-session-persistence` |
| 桌面版 CLI 可能有交互式依赖 | 非交互模式行为异常 | `-p` 已验证可用 |
| worktree 与 Supervisor baseline 冲突 | 审计基线不完整 | 默认不启用 worktree |

## 8. 不在本方案范围内

- 修改 `team_supervisor.py` 的 `TEAM_ORDER` / `ROLE_PROFILES`
- 修改 `registry.py` 的硬编码 agent 列表
- ChatGPT 聊天记忆集成（不了解该机制）
- CodeBuddy 的 MCP server 配置
- 沙箱模式（`--sandbox`）集成
