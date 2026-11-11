# Radar V2 Milestone 3：历史数据只读适配器（2026-10-11）

## 已完成（已经在本机实际跑过）

- 新增 tracker_history.py：根据明确的 run_id、workspace_id、scope 从 V1 read_model.py 只读获取快照。
- 新增 test_tracker_history.py：11 项历史数据及回放链路测试，均使用临时 SQLite 数据库。
- tracker_scheduler.py 的 ScanResult 增加 previous_radar_run_id 绑定校验，拒绝错误的前后扫描配对。
- V2 专项测试为 69/69，全项目测试为 260/260；未修改 V1 原有源码。

## 实际边界及安全

- 历史读取使用 SQLite mode=ro + query_only，绝不在读取操作时创建或迁移 V1 表。
- 既有运行必须同时匹配 workspace_id、scope 和显式 run_id，拒绝跨工作区数据。
- 如果源扫描携带 scan_errors、scanned_count 为 0、快照为空或排序身份异常，拒绝生成差异。
- 禁止把错误历史快照当作机会消失；时序倒置和历史 run 不匹配也会导致失败。
- 完整链路已在临时 V1 SQLite 表中验收：首次 run + 第二次 run -> new/removed/score_down -> V2 tracker_diffs。
- 回放适配器是历史快照连接器，不代表生产 Provider 已接入自动定时扫描。

## 生产数据库只读观察

- .radar/radar.db 存在，查询时只读打开。
- radar_runs 为 10 行；opportunity_snapshots 为 42 行；workspaces 为 0 行。
- 同工作区同范围的两次以上历史 run 组数为 0。
- 因此目前还没有条件进行「同工作区相邻两次真实扫描」的生产回放。
- 只读查询前数据库主文件 SHA256 前 20 位：1bd03ddfd15530ac2794。

## 下一步阻断项

1. 建立实际工作区及至少两次可核验的成功 Radar run；执行前明确数据来源和 API 预算。
2. 将只读历史回放与真实扫描调度入口接线，并验证扫描错误拒绝、异常恢复。
3. 生成 tracker_alerts（先仅存储，不发送通知），再提供独立只读 /v2/ API。
4. 集成 AI Team Dashboard 的真实 worker/任务进度；OpenCode 正式注册 Supervisor。

## 可复现验收

    python3 -m unittest test_tracker_history test_tracker_scheduler test_tracker test_radar_v2_diff -q
    /opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py' -q

## P2 生产运行接线 — 2026-11-11

- 已新增 tracker_runtime.py，将 V2 scheduler 绑定现有 run_discovery()，保留 V1 read-model 写入链路，不改 V1 评分与 Provider 实现。
- 服务启动时幂等初始化 V2 四张表，并为启用工作区创建默认关闭的 0 */6 * * * schedule；后台线程每 30 秒检查 due schedule。只有用户通过本机 API 显式启用 schedule 或手动 run 才会触发外部采集。
- 本机手动触发：POST /v2/workspaces/{workspace_id}/run；创建计划：POST /v2/workspaces/{workspace_id}/schedule；执行到期任务：POST /v2/tick。三个写接口均限 localhost。
- 只使用免费公开源；不传 GSC、Google Ads、网站抓取或付费翻译凭据。Alert 只入库，不投递外部渠道。
- 首次 V2 run 可绑定同工作区有效 V1 基线；后续 run 只允许使用前一条成功 V2 radar_run_id。低于 magnitude 10 的常规变化不告警，duplicate_id 仍作为完整性提醒。
- 新增 runtime/API/基线测试；测试不触发真实外部 Provider。生产 SQLite 已备份，V2 schema 已初始化，8787 服务重启后 /v2/health 返回 ok；默认 schedule 保持关闭。
