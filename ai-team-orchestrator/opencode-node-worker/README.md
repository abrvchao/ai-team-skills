# OpenCode Node Worker（OpenCode 执行器）

这是 AI Team 的独立研究/审查 Worker：通过 OpenCode CLI 使用一个**受控的免费模型池**，
不依赖 DSH / WorkBuddy CLI，默认 **只读**。

## 已执行验收

- Mac 当前 CLI `1.18.30`；已有旧单模型 Worker 在 Bridge 注册且在线。
- **多模型池是新的本地代码，尚未重启 Worker、尚未进行各模型的真实推理验收。**
- 单测：`node --test opencode-node-worker/test/*.test.js`（全合成，不联网）。
- 权限：只允许维护者审核的固定免费模型池，禁止 `--auto`；
  不允许 implementation/repair 阶段，不暴露模型的编辑工具权限。
- Artifact（产物）：仅 Markdown/TXT，限制工作区路径，不覆盖已有文件，
  验证 symlink 父目录不得指向工作区外。
- 错误/状态：新版本的推理必须显式打开 `AI_TEAM_OPENCODE_LIVE_APPROVED=1`，
  并在每次调用前验证**一小时内**的本地模型目录价格；校验失败时不会启动模型进程。
  真实预检通过才向 Bridge 报告就绪，超时/空响应均失败。

## 免费模型池 Free model pool（V1）

| 完整模型 ID | 适合任务 |
|---|---|
| `opencode/mimo-v2.6-flash-free` | 中文研究 / 默认 |
| `opencode/deepseek-v4.1-flash-free` | 代码审查 |
| `opencode/glm-5.3-flash-free` | 方案综合 |
| `opencode/nemotron-3.5-lightning-free` | 独立审查 |
| `opencode/longcat-2.5-preview-free` | 备选审查 |
| `opencode/space-bunny-free` | 备选审查 |
| `opencode/big-pickle` | 备选审查（名字不带 free，必须严格查价） |

以上是本机缓存的 **2026-10-18 离线价格状态**，不是永久免费保证。
`~/.cache/opencode/models.json` 有官方提供商 `opencode` 模型目录，必须恰好匹配
`cost.input === 0`、`cost.output === 0`，存在的 `cache_read` / `cache_write` 也必须是数字 0；
`status=deprecated` / `unavailable` 或者超过一小时未更新时**全部拒绝**。
这套控制不能证明服务端未来绝不会调整计费，用户仍需自行留意账户用量。

同一逻辑 Worker 在不同任务使用不同模型：由任务可信元数据
`metadata.opencode_model` 选定；未设置则使用环境变量 `OPENCODE_MODEL`，
再默认使用 MiMo。无效/收费模型会被拒绝，**没有自动免费到付费的回退**。
示例（任务元数据，并非完整 Bridge API 请求）：

```json
{
  "team_phase": "review",
  "expected_artifacts": ["review.md"],
  "opencode_model": "opencode/nemotron-3.5-lightning-free"
}
```

**这仍是一个 OpenCode 逻辑 Worker，不是 7 个独立同时并行的 Worker。**
要实现独立并行，后续才考虑多个隔离的 Agent ID、任务配额和成本上限，
不能因为列出了七个模型就宣布已有七个 Worker 在线。

OpenCode CLI 的 `--title` 固定，降低附带的自动标题模型调用风险；
每个任务使用 `--pure --agent plan`，由 Worker 将模型文本结果保存为 Markdown，
不允许模型直接编辑工程文件。

## 运行配置

需要 opencode CLI 已安装、Bridge 已启用 pull-agent opencode；
启动脚本只传递 Bridge token **文件路径**，不写入任何 token 内容。
OpenCode CLI 的账号/模型状态由 CLI 自身管理。
启用新版前必须先确认用户批准真实免费模型请求，再设置
`AI_TEAM_OPENCODE_LIVE_APPROVED=1`，手动启动与验证；
**本轮没有调整正在运行的 LaunchAgent，也没有执行新版本模型 preflight。**

本版本与 MiMo 独立 Worker 使用相似的底层模型，**两者不构成模型多样性意义上的完全独立审查**。
仍需要 DSH/Qwen/WorkBuddy 对工程结果做独立验证。

不允许自动调用火山引擎，也不会执行代码修改。
