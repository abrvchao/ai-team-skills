# AI Team Project Dashboard（AI Team 项目进度面板）

Local URL（本机地址）: `http://127.0.0.1:8770/`

## Purpose（用途）

把已经存在的 truth sources（真相源）聚合到一个只读视图，不建立第二套状态数据库：

- V1 Program（V1 总计划）与 M0–M3；
- Current Product（当前产品）及 R0–R4；
- Workers / Execution Surfaces（模型执行器 / 执行面）；
- Backlog / Blockers（待办 / 阻塞）；
- Recent Supervisor Runs（最近主管运行）；
- Artifact paths（成果路径）；
- Next Action（下一动作）。

## Runtime（运行方式）

- Python standard library（Python 标准库）`ThreadingHTTPServer`；
- 只绑定 `127.0.0.1`，不暴露到局域网/公网；
- LaunchAgent（用户级后台服务）label: `ai.team.dashboard`；
- 状态接口：`GET /api/status`；
- 健康接口：`GET /health`。

## Independent verification（独立验证）

Browser Worker（浏览器执行器）真实任务 `task_b42cdfbdcaba18bc02aa` 打开 dashboard（面板）并产出：

- `browser_report.json（浏览器报告）`；
- `page.html（渲染页面结构）`；
- `page.png（页面截图）`；
- final task state（最终任务状态）: `completed（完成）`。
