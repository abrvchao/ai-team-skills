# Radar V2 提醒闭环任务说明（2026-10-11）

## 已核验（不能重复造轮子）

- tracker.py 的 V2 结构已有 tracker_alerts 表。
- TrackerStore.create_alerts 已根据 diff_id + alert_type 形成确定性 alert_id，通过 INSERT ON CONFLICT DO NOTHING 去重。
- TrackerStore.list_alerts 可根据 workspace_id 查询提醒，默认不发送外部通知。
- tracker_scheduler.py 目前仅调用 record_diffs + complete_run，并没有建立任何 tracker_alerts。
- Radar V2 P0 / 历史回放曾在临时 SQLite 中验证两次真实历史扫描的差异，但不代表已经有两次新的 Provider 实时采集。
- 运行中的生产 .radar/radar.db 不允许本轮写入。

## 本轮唯一实施目标

让 TrackerScheduler 完成一轮有效差异记录后，安全地产生和持久化对应的提醒：

1. 尽量使用新增 tracker_alerts.py 纯函数，为 new / removed / rank_up / score_up / score_down 等变化建立可解释的提示，保留必要的标题和严重程度，不夸大“退出 Top-N”为互联网消失。
2. 通过已有 TrackerStore.create_alerts 落库，保证同一 diff 的同类提醒幂等、重复 tick 不重复提醒。
3. 数据源失败、空扫描、不完整历史快照时，不得产生 diff 或 alert。
4. 默认只入库、不发微信、短信、邮件或任何第三方通知。
5. 检查 workspace_id 作用域，避免其他工作区的 diff/alert 交叉污染。

## 代码改动边界

- 优先仅修改或新增：tracker_scheduler.py、test_tracker_scheduler.py、
  tracker_alerts.py、test_tracker_alerts.py。
- 不改 read_model.py、api.py、radar_v2_viewer.py、生产数据库、Bridge、火山引擎或其他 Worker。
- 不运行真实 Provider，不读取或复制 API Key，不修改外部服务限制。
- 不覆盖现有未提交代码，不使用 git reset --hard。

## 真实验收

- 在临时 SQLite 中实现首次 scan + 第二次 scan，确认 2 次成功执行、diff 和对应提醒。
- 用重复 tick、数据缺失、历史来源不一致等反例验证无虚假提醒。
- 运行 python3 -m unittest test_tracker test_tracker_scheduler test_tracker_alerts -q。
- 运行 /opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py' -q。
- 所有性能、成功/失败数都必须来自实际执行的日志或测试输出。

## 交付

- 文件级实施报告、实际测试结果、已知限制、回滚策略。
- 必须由与实施者不同的 Worker 做独立测试验收。
