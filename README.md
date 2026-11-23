# AI Team Skills

Reusable skills for an evolving AI team workflow.

## Included skills

- [`ai-team-supervisor`](ai-team-supervisor/SKILL.md): 主持或交接 AI Team；复用现有 MCP/Bridge，检查成员、派发任务、追踪证据并验收交付。

- `wechat-official-account-publisher`: research, draft, fact-check, review, format, and prepare articles for a WeChat Official Account draft box.

> The WeChat skill creates drafts only. It does not automatically publish public posts.

## 安装 AI Team 主持 skill

将仓库中的 `ai-team-supervisor/` 整个目录复制到 agent 的技能目录。Codex 默认目录为 `~/.codex/skills/`；其他支持 SKILL.md 的 agent 使用各自的技能目录，或直接把该文件交给 agent 读取。客户端仅需 Python 3.10+ 标准库。

示例指令：

> 使用 $ai-team-supervisor 接手我的 AI Team 项目。先读取已有交接，检查 Bridge 的成员和任务，再继续完成未完成项。

主持者使用运行它的 agent 自身配置的模型，无需新增主持者 API Key。执行端仍使用原有 Worker 配置。本 skill 需要已运行的 AI Team Bridge（默认本机 8765）或已连接的 AI Team MCP；它不会安装/启动 Worker，也不会自动修复离线模型。

```bash
python3 ai-team-supervisor/scripts/team.py doctor
python3 ai-team-supervisor/scripts/team.py agents
python3 -m unittest discover -s ai-team-supervisor/tests -v
```

Dashboard（面板）继续使用现有只读实现。本次选择可交接 skill 方案，没有增加前端模型密钥输入或网页主持功能。现有运行工作区的存量修改不属于此 skill 的源码依赖；完整新部署需按 `ai-team-orchestrator/README.md` 配置运行端。
