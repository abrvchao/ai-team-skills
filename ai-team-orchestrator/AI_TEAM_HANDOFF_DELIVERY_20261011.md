# AI Team 自动检测与交接能力交付（2026-10-11）

## 用户目标

不再依赖使用者手动记忆旧 Worker、接任者、前任进度与验收标准。
系统自动发现成员变化；失败改派时必须留下可检查的交接记录。

## 已实现功能

1. team_handoff.py：
   WorkerRosterMonitor 依托 Supervisor daemon 的现有轮询，自动对比 Bridge
   的登记、在线和能力信息。将最新状态和变动事件分别记录在
   _worker-roster.json、_worker-roster-events.jsonl。首次启动只建基线，
   Bridge 返回空列表时不生成全员掉线的假事件。
2. 任务失败/取消/超时后的有界重派：
   在重派前生成独立 JSON 交接凭证及接任工作区 HANDOFF.md，
   保留原任务 ID、前任 Worker、失败分类、之前尝试的角色、
   已验证产物的路径和原验收项，并将交接摘要加到接任任务提示。
3. 明确交接状态：
   assigned（已派工）、in_progress（已开始）、delivered（有可用产物）、
   failed（接任失败）。不把领取、进度百分比、空文件视为交接完成。
   delivered 仍然不代表最终的独立产品级 QA 验收通过。
4. 新 Worker 上线时，自动检查此前因缺少健康的独立验证者而
   blocked（阻塞）的任务，符合既有 Worker 健康、独立性策略才恢复，
   不抢占正在健康执行的旧任务。
5. project_dashboard.py 增加成员变动和交接看板：
   展示谁交给谁、交接状态、最新 Worker 变动；数据只读。
6. 测试覆盖变动去重、凭据过滤、假掉线保护、真实失败重派、
   阶段继承、接任验收门禁、交接记录复用/不可覆盖等风险。

## 已核验结果

- team_handoff / team_supervisor / project_dashboard 共 71/71 单测通过。
- 使用项目的 .venv/bin/python3 环境完成全量 137/137 测试（最后的文档更新不影响测试）。
- Python 语法及网页两个 JavaScript 脚本静态检查通过。
- macOS 中 ai.team.dashboard 服务已创建并启动，绑定 127.0.0.1:8770；
  主页、健康检查及 /api/status 均返回 HTTP 200。
- Supervisor 已重载，监测基线实际创建，包含 14 个登记 Agent；
  Bridge 仍确认 5 个在线 Worker：DSH、Qwen、MiMo、WorkBuddy、OpenCode。

## 当前真实障碍

- Radar 的 run_e8f273091e1a5d09259c 仍为 blocked。
  因为本轮历史任务的局部 Worker 健康状况只有实施者 Qwen 为 healthy，
  其他 Worker 虽然全局在线，但在该轮次被记录为 degraded/unhealthy。
  独立验证者门禁不能被省略或强行标绿。未来具备健康的新独立 Worker
  后，Supervisor 会尝试按策略恢复。
- 当前版本的任务交接专注于失败重派与成员变动。
  对阶段切换的完整交接产品化及跨主机原子派单，仍需后续增强。
- 不调用火山引擎、不修改生产 Radar 数据库、不删除既有产物。

## 回退

- 恢复原 Supervisor 代码可关闭交接监测/重派记录，不影响原任务库。
- 临时停止 ai.team.dashboard LaunchAgent 不影响 Bridge/Worker 派工。
- 所有改动保持在现有工作树，未执行 git reset、清理或强制提交。
