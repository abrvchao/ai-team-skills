# Radar / AI Team — Agent 上下文交接

> 这是公开给项目 Worker 的任务上下文，不是 ChatGPT 的私人记忆同步。

## 当前产品目标

- Radar 价值主张：先发现值得做的事，再决定怎么做。
- 当前优先级：Radar Lite 稳定、可读、多语言；随后改善机会质量和 AI Team 行动交接。
- 工作区 V1 已实现创建、编辑名称/主题/语言、工作区选择和保留旧数据。
- 原始证据及评分不可被翻译结果改写；翻译在界面层附加，失败需原文回退。
- 项目历史 Git 工作区较脏，未经核对不要清理、覆盖、重置或提交别人的修改。

## 分工和验证规则

- Supervisor 负责任务投递和状态；只有任务实际 started/completed 且有产物才算进展。
- 不同 Worker 必须在隔离工作区/不同文件范围开发，最终统一集成和交叉审查。
- 代码注释优先中文；英文专业术语补中文解释。
- 本地测试：在 content-opportunity-radar 下运行 `/opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py'`，另检查 `node --check dashboard/app.js`。
- Browser 可视化验收与单元测试是两种不同证据，不得混淆。

## 记忆边界

Worker 不会自动获得用户与 ChatGPT 的全部历史对话、个性化记忆或其他 Worker 的临时上下文。
每项任务须明确目标、输入资料、约束、完成条件、输出文件和交接方式；共享结论以版本化项目文档与 artifact 为准。
