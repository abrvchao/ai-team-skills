# Radar V2 Milestone 2 实施记录（2026-10-11）

## 已落地

- tracker.py / test_tracker.py：WorkBuddy 提供的 V2 存储层，28 项测试通过。
- radar_v2_diff.py / test_radar_v2_diff.py：从隔离产物提升到项目根目录，21 项测试通过。
- tracker_scheduler.py / test_tracker_scheduler.py：本轮新增调度器，9 项测试通过。
- V2 专项 **58/58**；Radar 全量回归 **249/249**。

## 安全边界

- 仅写 V2 新文件；不启动网络采集、不启动 cron daemon、不修改生产数据库。
- UTC 五段式 cron，分钟字段限制为单一值，最密每小时一次。
- 到期 schedule 采用原子条件 UPDATE 抢占，重复 tick 不会重复调度。
- 注入 scan callback；失败、不完整、空扫描或缺失历史快照不产生 diff/alert。
- 错误消息只存分类，不保存供应商原始错误及凭据。
- 目前调度器只经过模拟扫描测试，还没有接线 V1 真实 Provider。

## 后续阻断项（尚未完成）

1. Milestone 3：只读精确查询 V1 历史 run/snapshot，校验 workspace、scope 和数据完整性。
2. 对比两次成功 run，连接 ScanResult 到真实扫描回调。
3. 持久化提醒与提醒发送分离，避免通知风暴。
4. 独立 /v2/ 只读 API 路由、工作区隔离测试、调用成本门槛。
5. 国产模型交叉审核及真实数据回放，确认生产库数据没有修改。

## 本轮测试命令

    python3 -m unittest test_radar_v2_diff test_tracker test_tracker_scheduler -q
    /opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py' -q
