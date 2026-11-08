# AI Team / Radar V2 本轮实际交付（2026-10-11）

## 项目总进度

| 阶段 | 本轮状态 | 实测证据 | 限制 |
|---|---|---|---|
| P0：历史真实数据闭环 | 已完成离线回放 | Radar 旧库 2026-10-07 两次真实扫描，产生 4 条 Top-N 差异 | 不代表新数据实时采集 |
| P1：Radar V2 页面/API | 已启动只读预览 | http://127.0.0.1:8791/、/health、/api/v2/summary 均 HTTP 200 | 仅本机，暂未替换生产 V1 页面 |
| P2：AI Team Dashboard | 已升级并启动本机预览 | http://127.0.0.1:8770/、/api/status、/api/tasks、/api/tasks/<task_id> 均 HTTP 200 | 当前只是 WebCodex 管理的限时运行，未设置永久自启动 |

## AI Team 真实数据

- Bridge 读取到 12 个 Agent 登记记录；验证时仅 Qwen 的 available=true。
- Bridge 共读取到 100 条任务记录；Dashboard 显示按 created_at 倒序的最近 24 条。
- 任务详情不复述完整 prompt 或消息正文；真实进度来自 Bridge 的 progress，真实产物来自 artifacts。
- 工作任务列表允许点击查看详情；任务 queued/started 由 Bridge 自身字段决定，不能自行推断“在运行”。
- 复用已有 project_dashboard.py，没有新建平行状态数据库。
- Dashboard 项目级用例 10/10 通过（含 3 条新增真实任务/Agent 测试）。

## Radar V2 P0 / P1 真实数据

- 原始数据库：content-opportunity-radar/.radar/radar.db，只读模式检查。
- 真实历史回放结果：content-opportunity-radar/.ai-team-runs/radar-v2-p0-historical-replay-20261011/
  - replay.db：独立的新工作区、两次映射的历史运行与 V2 差异。
  - summary.json：源扫描 ID、生成时间、映射 ID、4 条变化及源文件哈希。
- 新增 radar_v2_p0_replay.py / test_radar_v2_p0_replay.py 与 radar_v2_viewer.py / test_radar_v2_viewer.py。
- 本轮 Radar 全量回归 270/270 通过；新增 P0/P1 针对性 10/10 通过。
- 数据库主文件 SHA256 在回放前后相同；本轮离线回放新增 Provider API 调用为 0。
- Radar 页面有中文、英文、日语、韩语切换；当前真实数据只读演示，无远程公网开放。

## 未完成项（不能标绿）

1. Radar V2 的真实工作区 Provider 采集/定时运行，必须先配置数据源和预算限额。
2. 根据完整性校验生成并存储结构化 tracker_alerts，再接 /v2/ 正式 API 和 Radar 主页面。
3. OpenCode 尚未作为 Supervisor pull Worker 注册；其本机 CLI 已验证指定 MiMo 免费模型真实响应，但此前完整代码审查超时。
4. WorkBuddy 独立 CLI 已安装，但认证、Bridge 注册和 Supervisor 派工尚未验收；Grok 仍保持暂停，Gemini 不自动充值调用。
5. 预览服务需正式持久化（launchd 或受控进程守护）、配置自动重启和定期健康检测。

## 运维/验收参考

    cd /path/to/ai-team-skills/content-opportunity-radar
    /opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py' -q

    cd /path/to/ai-team-skills/ai-team-orchestrator
    python3 -m unittest test_project_dashboard -q

以上是真实当轮验证记录。没有重启生产 Bridge、没有 git commit、没有 reset/clean。
