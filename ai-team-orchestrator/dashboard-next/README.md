# AI Team Dashboard v2（独立升级版）

> 状态：GitHub 功能分支 / 草稿 PR。**尚未安装到用户 Mac**。GitHub `main` 没有本机的 `project_dashboard.py` / `team_monitor.py`，因此不能直接覆盖正在运行的 Dashboard。

## 本轮实际交付

`dashboard-next/` 提供一个现代化、响应式的只读前端，不改 Bridge 和旧面板。它仅访问现有 Dashboard 同源接口：

- `GET /api/monitor`：Supervisor Run 进度、阶段、子任务、Worker 的真实上报进度；`bridge_available` 必须是布尔 `true` 才能声称在线。
- `GET /api/status`：已有产品与社会数据源排期。若没有 `social_sources`，明确显示「计划数据未同步」，不补造五平台状态。
- 若 API 失败：保留上次成功数据的本页内存快照，显著显示历史数据状态；Worker 不再展示为实时在线。
- 若 Worker 没有上报百分比，显示「进度未上报」，不把 task=completed 自动转成 100%。项目百分比始终标注为 **Supervisor 工作流完成率**，不是产品验收率。
- 可按全部 / 执行中 / 阻塞或失败 / 完成筛选项目；项目可展开各阶段、每个子任务和真实 Worker 报告。
- 包含简体中文、英文、日文和韩文界面文案（来源任务标题仍保留原文，不凭空机翻）；自适应桌面和手机视图。
- 所有动态数据显示都通过 `textContent`，不从 API 构造 HTML。HTML 包含严格的同源 CSP。
- 每 12 秒尝试刷新；手动刷新、每个请求 6 秒超时。无覆盖或破坏式 API 调用。

## 真实验收及使用范围

离线语义测试：

```bash
cd ai-team-orchestrator
node --test dashboard-next/test_data.js
node --check dashboard-next/app.js
python -m unittest -v test_dashboard_next test_dashboard_next_server
```

用 GitHub Actions 运行再判断是否通过。**这些测试不证明 Mac 实际部署成功，也不证明 Bridge 真实响应。**

## 独立运行方式（Mac 恢复后才可执行部署）

无需更改或重启旧版 `project_dashboard.py`。新版独立监听回环地址 `127.0.0.1:8772`，仅读取旧版 `127.0.0.1:8770` 的 `/api/monitor`、`/api/status`：

```bash
cd ai-team-orchestrator
python dashboard-next/server.py --port 8772 --upstream http://127.0.0.1:8770
# 浏览器打开 http://127.0.0.1:8772/
```

服务只绑定本机回环地址，仅开放固定静态资源、`/health` 以及两个只读代理接口；拒绝 POST/PUT/DELETE 与路径遍历。上游失效时代理返回 502，前端将旧数据显示为历史快照，不会误判 Worker 在线。

**以上是安装说明，并不表示该端口当前已经部署/能访问。**

## Mac Tunnel 恢复后的集成步骤（不能自动假定成功）

1. 通过 WebCodex 只读检查 Mac 的 `project_dashboard.py`、`team_monitor.py`、`/api/status`、`/api/monitor` 返回字段。检查是否已有 P0 社交数据源任务，避免重复派工。
2. 优先采用独立 `server.py` 在 `127.0.0.1:8772` 启动，不覆盖经典 `127.0.0.1:8770`；如果后续决定整合为旧服务同源路由，需独立做兼容审查。
3. 本机运行 Python + JS 专项和全量回归，浏览器实测多语言、离线/过期状态、Worker 任务展开、数据源计划来源。未部署前不要向用户发布 `/v2/` 作为有效入口。
4. 稳定后再评估将 v2 作为默认首页。必须可快速回退，不开放未授权的公网访问。

## 尚未覆盖

- 本轮不能替代后端修复：Bridge/Docker 文件系统等待仍需 Mac 在线诊断。
- DSH D1–D4 `accept` 与旧 Run `blocked` 的证据对账必须由后端提供独立的、可追溯的 QA 对象；当前 UI **不会猜测验收已通过**。
- Worker 的任务历史受 Bridge 最近任务抽样范围限制，不能当作无限期审计日志。
- `/api/monitor` 的任务信息若带敏感字段，后端仍必须最小化数据，前端不替代权限控制。
