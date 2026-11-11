# AI Team 独立总控面板：项目 / 子任务 / Worker 实时进度

## 用户入口

- 本机独立网页：http://127.0.0.1:8770/
- 启动服务：macOS LaunchAgent ai.team.dashboard，登录后自动启动。
- 仅绑定 127.0.0.1；不能从局域网其他电脑或互联网直接访问。
- 不需要打开 ChatGPT 或 WebCodex；可在 Mac 浏览器收藏。

## 本轮实现

- team_monitor.py：只读聚合 Bridge（最近一页任务、在线状态、真实进度事件）
  与 Supervisor（项目阶段、子任务分工、历史状态、阻塞与下一动作）。
- project_dashboard.py：新增 /api/monitor JSON 接口及两个优先显示区域：
  「实时项目与子任务进度」和「Worker 当前工作与进度」。
- 展开项目可看各阶段和全部已记录子任务的 Worker、任务 ID、执行状态、
  真实进度事件、可点击的任务详情和已登记产物。
- Worker 卡片展示在线/离线、空闲/执行中、当前活跃任务、最近任务样本的
  已完成及失败数量、最近上报的进度。
- 页面约每 7 秒刷新。保留已展开项目的展开状态。
- 前端动态任务标题/进度内容全部用 DOM textContent 填入，避免 HTML 注入。

## 百分比数据的解释

- 项目/阶段百分比：Supervisor 的阶段流程进度（不代表产品级 QA 通过率）。
- 子任务与 Worker 百分比：只从 Bridge 的真实 Worker 进度事件读取。
  未上报时显示「进度未上报」，不补估值。
- 最近任务统计：Bridge /tasks 当前只返回最近 100 条，不是累计历史总数；
  新界面在底部明确展示抽样范围。
- 每轮最多读取 16 条任务的详细进度事件，优先正在执行的任务，
  再选每个 Worker 最近一次任务，避免仪表盘产生过度轮询。
- Bridge 不可达时仍显示 Supervisor 已保存的 Run 子任务，
  并明确标记实时进度数据可能过期。

## 验收记录

- test_team_monitor.py 与 test_project_dashboard.py 相关测试 19/19 通过。
- 项目虚拟环境 .venv/bin/python 全量测试 145/145 通过。
- Python 模块静态检查和页面 JavaScript 语法检查通过。
- 实际本机 /、/health、/api/status、/api/monitor 均返回 HTTP 200。
- Chrome Headless DOM 渲染实际能看到最新 Radar Run、任务 ID 和 14 个 Worker 卡片。
- 本轮没有修改生产 Radar 数据库，没有调用火山引擎，
  没有部署任何公网访问入口。

## 当前真实限制

- Mac 之外的设备如需访问，需另外配置经过认证、加密的访问通道，
  不能直接把 8770 端口对公网开放。
- 本轮聚合最近 8 个 Supervisor Run；更久的历史 Run 保存在原 JSON 中。
- 若 Worker 没有真实进度事件，只能展示任务状态，不能推算百分比。
