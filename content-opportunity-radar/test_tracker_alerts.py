"""Radar V2 提醒生成器单元测试。

全部使用临时 SQLite，绝不触碰生产 ``.radar/radar.db``。
测试数据为隔离的临时 SQLite 数据，非真实 Provider 采集。

覆盖：
- 各 change_type 的 alert 文案生成（title / summary）；
- 幂等性：重复调用 ``create_alerts`` 不产生重复记录；
- 工作区隔离：不同 workspace 的 alert 互不可见；
- 文案合规：禁止「消失」「全网」「下架」；
- 空 diff 列表不产生 alert；
- severity 分级正确。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracker import TrackerStore
from tracker_alerts import (
    build_alerts_from_diffs,
    validate_alert_copywriting,
    _severity_for,
)

T0 = "2026-10-11T00:00:00+00:00"
T1 = "2026-10-11T06:00:00+00:00"

WORKSPACE_A = "ws_alert_a"
WORKSPACE_B = "ws_alert_b"


class AlertBuilderBase(unittest.TestCase):
    """公共脚手架：临时库 + 两个工作区。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "radar.db"
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspaces ("
            " workspace_id TEXT PRIMARY KEY, name TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)",
            (WORKSPACE_A, "工作区A", T0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)",
            (WORKSPACE_B, "工作区B", T0),
        )
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _make_run(self, workspace_id: str = WORKSPACE_A) -> tuple[dict, dict]:
        """创建 schedule + run，返回 (schedule, run)。"""
        schedule = self.store.create_schedule(workspace_id, next_due_at=T0)
        run = self.store.start_run(
            schedule_id=schedule["schedule_id"],
            workspace_id=workspace_id,
            scheduled_at=T1,
        )
        return schedule, run

    def _record_and_list_diffs(
        self, tracker_run_id: str, workspace_id: str, diffs: list[dict]
    ) -> list[dict]:
        """写入 diff 并取回（含 store 生成的 diff_id）。"""
        self.store.record_diffs(
            tracker_run_id=tracker_run_id,
            # 不同工作区的 V1 扫描应拥有不同的全局 run_id。
            # 固定同一个 run_id 会人为制造 diff_id 主键冲突，误报隔离失败。
            curr_radar_run_id=f"run_curr_alert_test_{workspace_id}",
            workspace_id=workspace_id,
            prev_radar_run_id=None,
            diffs=diffs,
            detected_at=T1,
        )
        return self.store.list_diffs(tracker_run_id=tracker_run_id)


class TestBuildAlertsFromDiffs(AlertBuilderBase):
    """纯函数 build_alerts_from_diffs 的单元测试（不涉及 DB）。"""

    def test_empty_diffs_produces_no_alerts(self):
        self.assertEqual(build_alerts_from_diffs([]), [])

    def test_new_change_type(self):
        diffs = [{
            "diff_id": "d001",
            "change_type": "new",
            "topic_name": "AI Agents",
            "topic_id": "topic:ai-agents",
            "curr_rank": 3,
            "magnitude": 100.0,
        }]
        alerts = build_alerts_from_diffs(diffs)
        self.assertEqual(1, len(alerts))
        self.assertEqual("d001", alerts[0]["diff_id"])
        self.assertEqual("new", alerts[0]["alert_type"])
        self.assertEqual("critical", alerts[0]["severity"])
        self.assertIn("新机会进入 Top-N", alerts[0]["title"])
        self.assertIn("AI Agents", alerts[0]["title"])
        self.assertIn("AI Agents", alerts[0]["summary"])

    def test_removed_change_type_uses_correct_copy(self):
        """removed 必须说「退出当前 Top-N 监控范围」，不得说「消失」「全网」「下架」。"""
        diffs = [{
            "diff_id": "d002",
            "change_type": "removed",
            "topic_name": "旧话题",
            "topic_id": "topic:old",
            "prev_rank": 15,
            "magnitude": 100.0,
        }]
        alerts = build_alerts_from_diffs(diffs)
        self.assertEqual(1, len(alerts))
        self.assertIn("退出当前 Top-N 监控范围", alerts[0]["title"])
        violations = validate_alert_copywriting(alerts)
        self.assertEqual([], violations)

    def test_rank_down_included(self):
        """rank_down 必须生成提醒（synthesis 要求不得遗漏）。"""
        diffs = [{
            "diff_id": "d003",
            "change_type": "rank_down",
            "topic_name": "下降话题",
            "topic_id": "topic:down",
            "prev_rank": 3,
            "curr_rank": 10,
            "magnitude": 70.0,
        }]
        alerts = build_alerts_from_diffs(diffs)
        self.assertEqual(1, len(alerts))
        self.assertEqual("rank_down", alerts[0]["alert_type"])
        self.assertIn("排名下降", alerts[0]["title"])

    def test_duplicate_id_alert_generated(self):
        """duplicate_id 作为数据完整性信号，也应生成提醒。"""
        diffs = [{
            "diff_id": "d004",
            "change_type": "duplicate_id",
            "topic_name": "重复话题",
            "topic_id": "topic:dup",
            "magnitude": 0.0,
            "details": {"side": "curr", "duplicate_count": 3},
        }]
        alerts = build_alerts_from_diffs(diffs)
        self.assertEqual(1, len(alerts))
        self.assertEqual("duplicate_id", alerts[0]["alert_type"])
        self.assertIn("数据重复告警", alerts[0]["title"])
        self.assertIn("3", alerts[0]["summary"])

    def test_severity_thresholds(self):
        self.assertEqual("critical", _severity_for(80.0))
        self.assertEqual("critical", _severity_for(100.0))
        self.assertEqual("warning", _severity_for(30.0))
        self.assertEqual("warning", _severity_for(79.9))
        self.assertEqual("info", _severity_for(0.0))
        self.assertEqual("info", _severity_for(29.9))

    def test_missing_diff_id_or_change_type_skipped(self):
        diffs = [
            {"diff_id": "", "change_type": "new", "topic_name": "x"},
            {"diff_id": "d1", "change_type": "", "topic_name": "x"},
            {"change_type": "new", "topic_name": "x"},
        ]
        self.assertEqual([], build_alerts_from_diffs(diffs))

    def test_all_change_types_covered(self):
        """确保所有 7 种 change_type 都能生成 alert，不遗漏。"""
        types = ["new", "removed", "rank_up", "rank_down", "score_up", "score_down", "duplicate_id"]
        diffs = [
            {"diff_id": f"d_{t}", "change_type": t, "topic_name": f"topic_{t}",
             "magnitude": 50.0, "prev_rank": 5, "curr_rank": 2,
             "score_delta": 10.0, "details": {"side": "curr", "duplicate_count": 2}}
            for t in types
        ]
        alerts = build_alerts_from_diffs(diffs)
        self.assertEqual(len(types), len(alerts))
        generated_types = {a["alert_type"] for a in alerts}
        self.assertEqual(set(types), generated_types)


class TestCopywritingCompliance(AlertBuilderBase):
    """文案合规：禁止「消失」「全网」「下架」。"""

    def test_no_forbidden_words_in_any_alert(self):
        """对所有 change_type 生成的 alert 做全量文案检查。"""
        types = ["new", "removed", "rank_up", "rank_down", "score_up", "score_down", "duplicate_id"]
        diffs = [
            {"diff_id": f"d_{t}", "change_type": t, "topic_name": f"话题_{t}",
             "magnitude": 100.0, "prev_rank": 5, "curr_rank": 2,
             "score_delta": -10.0, "details": {"side": "curr", "duplicate_count": 2}}
            for t in types
        ]
        alerts = build_alerts_from_diffs(diffs)
        violations = validate_alert_copywriting(alerts)
        self.assertEqual([], violations, f"文案违规: {violations}")

    def test_validate_detects_forbidden_words(self):
        bad_alerts = [
            {"diff_id": "x", "title": "机会消失了", "summary": "s"},
            {"diff_id": "y", "title": "t", "summary": "全网热门"},
            {"diff_id": "z", "title": "已下架", "summary": "s"},
        ]
        violations = validate_alert_copywriting(bad_alerts)
        self.assertEqual(3, len(violations))


class TestAlertPersistenceIdempotency(AlertBuilderBase):
    """通过 TrackerStore 验证：重复 tick 不产生重复/虚假提醒。"""

    def test_idempotent_alert_insertion(self):
        """同一 diff 的 alert 重复写入应被 ON CONFLICT 吞掉。"""
        _, run = self._make_run()
        diffs_in = [
            {"topic_id": "topic:alpha", "topic_name": "Alpha", "change_type": "new",
             "curr_rank": 1, "magnitude": 95.0},
        ]
        persisted_diffs = self._record_and_list_diffs(
            run["tracker_run_id"], WORKSPACE_A, diffs_in
        )
        alerts = build_alerts_from_diffs(persisted_diffs)
        # 第一次写入
        n1 = self.store.create_alerts(
            tracker_run_id=run["tracker_run_id"],
            workspace_id=WORKSPACE_A,
            alerts=alerts,
            created_at=T1,
        )
        # 第二次写入（模拟重复 tick）
        n2 = self.store.create_alerts(
            tracker_run_id=run["tracker_run_id"],
            workspace_id=WORKSPACE_A,
            alerts=alerts,
            created_at=T1,
        )
        self.assertEqual(1, n1)
        self.assertEqual(0, n2)
        stored = self.store.list_alerts(workspace_id=WORKSPACE_A)
        self.assertEqual(1, len(stored))

    def test_workspace_isolation(self):
        """不同 workspace 的 alert 互不可见。"""
        _, run_a = self._make_run(WORKSPACE_A)
        _, run_b = self._make_run(WORKSPACE_B)

        diffs_a = self._record_and_list_diffs(
            run_a["tracker_run_id"], WORKSPACE_A,
            [{"topic_id": "topic:shared", "topic_name": "共享话题",
              "change_type": "new", "curr_rank": 1, "magnitude": 90.0}],
        )
        diffs_b = self._record_and_list_diffs(
            run_b["tracker_run_id"], WORKSPACE_B,
            [{"topic_id": "topic:shared", "topic_name": "共享话题",
              "change_type": "new", "curr_rank": 1, "magnitude": 90.0}],
        )
        alerts_a = build_alerts_from_diffs(diffs_a)
        alerts_b = build_alerts_from_diffs(diffs_b)

        self.store.create_alerts(
            tracker_run_id=run_a["tracker_run_id"],
            workspace_id=WORKSPACE_A,
            alerts=alerts_a,
        )
        self.store.create_alerts(
            tracker_run_id=run_b["tracker_run_id"],
            workspace_id=WORKSPACE_B,
            alerts=alerts_b,
        )
        list_a = self.store.list_alerts(workspace_id=WORKSPACE_A)
        list_b = self.store.list_alerts(workspace_id=WORKSPACE_B)
        self.assertEqual(1, len(list_a))
        self.assertEqual(1, len(list_b))
        self.assertNotEqual(list_a[0]["alert_id"], list_b[0]["alert_id"])

    def test_no_delivery_by_default(self):
        """alert 默认 delivered=0，不发送任何第三方通知。"""
        _, run = self._make_run()
        diffs = self._record_and_list_diffs(
            run["tracker_run_id"], WORKSPACE_A,
            [{"topic_id": "topic:x", "topic_name": "X", "change_type": "new",
              "curr_rank": 1, "magnitude": 50.0}],
        )
        alerts = build_alerts_from_diffs(diffs)
        self.store.create_alerts(
            tracker_run_id=run["tracker_run_id"],
            workspace_id=WORKSPACE_A,
            alerts=alerts,
        )
        stored = self.store.list_alerts(workspace_id=WORKSPACE_A)
        self.assertEqual(1, len(stored))
        self.assertEqual(0, stored[0]["delivered"])
        self.assertIsNone(stored[0]["delivered_at"])


class TestEndToEndAlertFlow(AlertBuilderBase):
    """端到端：两次隔离扫描产生真实 diff 和对应提醒。"""

    def test_two_scans_produce_diffs_and_alerts(self):
        """模拟两次 scan：第一次 bootstrap，第二次检测到变化。"""
        _, run1 = self._make_run()
        # 第一次扫描：bootstrap（无 prev）
        diffs1 = self._record_and_list_diffs(
            run1["tracker_run_id"], WORKSPACE_A,
            [
                {"topic_id": "topic:a", "topic_name": "话题A", "change_type": "new",
                 "curr_rank": 1, "magnitude": 0.0},
                {"topic_id": "topic:b", "topic_name": "话题B", "change_type": "new",
                 "curr_rank": 2, "magnitude": 0.0},
            ],
        )
        alerts1 = build_alerts_from_diffs(diffs1)
        n1 = self.store.create_alerts(
            tracker_run_id=run1["tracker_run_id"],
            workspace_id=WORKSPACE_A,
            alerts=alerts1,
        )
        self.assertEqual(2, n1)

        # 第二次扫描：话题A 消失（removed），话题C 新增（new）
        schedule2, run2 = self._make_run()
        diffs2 = self._record_and_list_diffs(
            run2["tracker_run_id"], WORKSPACE_A,
            [
                {"topic_id": "topic:a", "topic_name": "话题A", "change_type": "removed",
                 "prev_rank": 1, "magnitude": 100.0},
                {"topic_id": "topic:c", "topic_name": "话题C", "change_type": "new",
                 "curr_rank": 3, "magnitude": 100.0},
            ],
        )
        alerts2 = build_alerts_from_diffs(diffs2)
        n2 = self.store.create_alerts(
            tracker_run_id=run2["tracker_run_id"],
            workspace_id=WORKSPACE_A,
            alerts=alerts2,
        )
        self.assertEqual(2, n2)

        all_alerts = self.store.list_alerts(workspace_id=WORKSPACE_A)
        self.assertEqual(4, len(all_alerts))

        # 验证 removed 文案
        removed_alerts = [a for a in all_alerts if a["alert_type"] == "removed"]
        self.assertEqual(1, len(removed_alerts))
        self.assertIn("退出当前 Top-N 监控范围", removed_alerts[0]["title"])
        violations = validate_alert_copywriting(all_alerts)
        self.assertEqual([], violations)


class TestBatchAtomicity(AlertBuilderBase):
    """D2 回归：record_diffs / create_alerts 批处理必须原子。"""

    def test_record_diffs_rolls_back_on_error(self):
        """批处理中途报错时，已插入的 diff 必须回滚，不留在连接中。"""
        _, run = self._make_run()
        diffs = [
            {"topic_id": "t1", "topic_name": "T1", "change_type": "new",
             "curr_rank": 1, "magnitude": 50.0},
            {"topic_id": "", "topic_name": "bad", "change_type": "new"},
        ]
        with self.assertRaises(ValueError):
            self.store.record_diffs(
                tracker_run_id=run["tracker_run_id"],
                curr_radar_run_id="run_atomic_1",
                workspace_id=WORKSPACE_A,
                diffs=diffs,
            )
        self.assertEqual([], self.store.list_diffs(tracker_run_id=run["tracker_run_id"]))

    def test_record_diffs_partial_not_persisted_by_later_commit(self):
        """批处理报错后，后续无关 commit 不得把残留行持久化到数据库文件。"""
        _, run = self._make_run()
        diffs = [
            {"topic_id": "t1", "topic_name": "T1", "change_type": "new",
             "curr_rank": 1, "magnitude": 50.0},
            {"topic_id": "", "topic_name": "bad", "change_type": "new"},
        ]
        with self.assertRaises(ValueError):
            self.store.record_diffs(
                tracker_run_id=run["tracker_run_id"],
                curr_radar_run_id="run_atomic_2",
                workspace_id=WORKSPACE_A,
                diffs=diffs,
            )
        schedule2 = self.store.create_schedule(WORKSPACE_B, next_due_at=T0)
        self.store.start_run(
            schedule_id=schedule2["schedule_id"],
            workspace_id=WORKSPACE_B,
            scheduled_at=T1,
        )
        self.store.conn.commit()
        self.assertEqual([], self.store.list_diffs(tracker_run_id=run["tracker_run_id"]))

    def test_create_alerts_rolls_back_on_invalid_severity(self):
        """create_alerts 遇到非法 severity 时整批回滚。"""
        _, run = self._make_run()
        persisted = self._record_and_list_diffs(
            run["tracker_run_id"], WORKSPACE_A,
            [{"topic_id": "t1", "topic_name": "T1", "change_type": "new",
              "curr_rank": 1, "magnitude": 50.0}],
        )
        good_alert = {"diff_id": persisted[0]["diff_id"], "alert_type": "new",
                      "severity": "info", "title": "t", "summary": "s"}
        bad_alert = {"diff_id": "dx", "alert_type": "new",
                     "severity": "INVALID", "title": "t", "summary": "s"}
        with self.assertRaises(ValueError):
            self.store.create_alerts(
                tracker_run_id=run["tracker_run_id"],
                workspace_id=WORKSPACE_A,
                alerts=[good_alert, bad_alert],
            )
        self.assertEqual([], self.store.list_alerts(workspace_id=WORKSPACE_A))

    def test_create_alerts_partial_not_persisted_by_later_commit(self):
        """create_alerts 报错后，后续无关 commit 不得把残留 alert 持久化。"""
        _, run = self._make_run()
        persisted = self._record_and_list_diffs(
            run["tracker_run_id"], WORKSPACE_A,
            [{"topic_id": "t1", "topic_name": "T1", "change_type": "new",
              "curr_rank": 1, "magnitude": 50.0}],
        )
        good_alert = {"diff_id": persisted[0]["diff_id"], "alert_type": "new",
                      "severity": "info", "title": "t", "summary": "s"}
        bad_alert = {"diff_id": "dx", "alert_type": "new",
                     "severity": "INVALID", "title": "t", "summary": "s"}
        with self.assertRaises(ValueError):
            self.store.create_alerts(
                tracker_run_id=run["tracker_run_id"],
                workspace_id=WORKSPACE_A,
                alerts=[good_alert, bad_alert],
            )
        self.store.conn.commit()
        self.assertEqual([], self.store.list_alerts(workspace_id=WORKSPACE_A))


class TestD3WorkspaceCollision(unittest.TestCase):
    """D3 回归：两个工作区使用完全相同的 radar_run_id 时不得静默冲突。

    验证 diff_id 包含 workspace_id，确保跨工作区隔离为不变量。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "radar.db"
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspaces ("
            " workspace_id TEXT PRIMARY KEY, name TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)",
            (WORKSPACE_A, "工作区A", T0),
        )
        conn.execute(
            "INSERT OR IGNORE INTO workspaces VALUES (?, ?, ?)",
            (WORKSPACE_B, "工作区B", T0),
        )
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_same_radar_run_id_different_workspaces_no_collision(self):
        """两个工作区使用完全相同的 curr/prev radar_run_id + topic + change_type，
        diff_id 和 alert_id 必须不同，不得静默丢失任一工作区的数据。"""
        sched_a = self.store.create_schedule(WORKSPACE_A, next_due_at=T0)
        run_a = self.store.start_run(
            schedule_id=sched_a["schedule_id"],
            workspace_id=WORKSPACE_A, scheduled_at=T1,
        )
        sched_b = self.store.create_schedule(WORKSPACE_B, next_due_at=T0)
        run_b = self.store.start_run(
            schedule_id=sched_b["schedule_id"],
            workspace_id=WORKSPACE_B, scheduled_at=T1,
        )

        # 关键：两个工作区使用完全相同的 radar_run_id
        shared_radar_run_id = "shared-radar-run-001"
        shared_diffs = [
            {"topic_id": "topic:shared", "topic_name": "共享话题",
             "change_type": "new", "curr_rank": 1, "magnitude": 90.0},
        ]

        # 工作区 A 写入 diff
        n_a = self.store.record_diffs(
            tracker_run_id=run_a["tracker_run_id"],
            curr_radar_run_id=shared_radar_run_id,
            workspace_id=WORKSPACE_A,
            prev_radar_run_id=None,
            diffs=shared_diffs,
            detected_at=T1,
        )
        # 工作区 B 写入 diff（相同的 radar_run_id + topic + change_type）
        n_b = self.store.record_diffs(
            tracker_run_id=run_b["tracker_run_id"],
            curr_radar_run_id=shared_radar_run_id,
            workspace_id=WORKSPACE_B,
            prev_radar_run_id=None,
            diffs=shared_diffs,
            detected_at=T1,
        )

        # D3 核心断言：两个工作区各插入 1 条 diff，不得静默丢失
        self.assertEqual(1, n_a, "工作区 A 应成功插入 1 条 diff")
        self.assertEqual(1, n_b, "工作区 B 应成功插入 1 条 diff（D3 修复前为 0）")

        diffs_a = self.store.list_diffs(tracker_run_id=run_a["tracker_run_id"])
        diffs_b = self.store.list_diffs(tracker_run_id=run_b["tracker_run_id"])
        self.assertEqual(1, len(diffs_a))
        self.assertEqual(1, len(diffs_b))
        # diff_id 必须不同（因为包含 workspace_id）
        self.assertNotEqual(diffs_a[0]["diff_id"], diffs_b[0]["diff_id"])

        # alert 也必须各自独立
        alerts_a = build_alerts_from_diffs(diffs_a)
        alerts_b = build_alerts_from_diffs(diffs_b)
        self.store.create_alerts(
            tracker_run_id=run_a["tracker_run_id"],
            workspace_id=WORKSPACE_A, alerts=alerts_a,
        )
        self.store.create_alerts(
            tracker_run_id=run_b["tracker_run_id"],
            workspace_id=WORKSPACE_B, alerts=alerts_b,
        )
        stored_a = self.store.list_alerts(workspace_id=WORKSPACE_A)
        stored_b = self.store.list_alerts(workspace_id=WORKSPACE_B)
        self.assertEqual(1, len(stored_a))
        self.assertEqual(1, len(stored_b))
        self.assertNotEqual(stored_a[0]["alert_id"], stored_b[0]["alert_id"])


if __name__ == "__main__":
    unittest.main()
