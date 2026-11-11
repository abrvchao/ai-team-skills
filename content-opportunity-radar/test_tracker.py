"""Radar V2 Milestone 1 —— TrackerStore 单元测试。

依据 `RADAR_V2_IMPLEMENTATION_PLAN.md` §8.3 的 `TestTrackerStore` 用例清单，
并补充若干工程必需用例（建表幂等、只读保护、不触碰 V1 表、外键链完整性）。

所有测试使用临时库（tempfile），绝不触碰项目真实 `.radar/radar.db`。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracker import (
    DEFAULT_CRON,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    TrackerStore,
)

# 时间常量：一律用显式 ISO8601 UTC 字符串，避免测试依赖真实时钟。
T0 = "2026-10-11T00:00:00+00:00"
T1 = "2026-10-11T06:00:00+00:00"
T2 = "2026-10-11T12:00:00+00:00"
PAST = "2026-10-10T00:00:00+00:00"
FUTURE = "2026-10-20T00:00:00+00:00"

WORKSPACE_ID = "ws_test_0001"


class TrackerStoreTestBase(unittest.TestCase):
    """公共脚手架：临时库 + 最小 workspaces 表（满足 V2 的外键引用）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "radar.db"
        # 真实环境里 workspaces 由 V1 创建；这里只造一个最小同构表。
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspaces ("
            " workspace_id TEXT PRIMARY KEY,"
            " name TEXT,"
            " created_at TEXT)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (workspace_id, name, created_at)"
            " VALUES (?, ?, ?)",
            (WORKSPACE_ID, "测试工作区", T0),
        )
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    # ── 辅助：构造 schedule → run → diff → alert 完整链路 ──────────────────
    def _make_schedule(self, **kwargs) -> dict:
        return self.store.create_schedule(
            WORKSPACE_ID, next_due_at=PAST, **kwargs
        )

    def _make_run(self, schedule_id: str) -> dict:
        return self.store.start_run(
            schedule_id=schedule_id,
            workspace_id=WORKSPACE_ID,
            scheduled_at=T1,
        )

    def _make_diff(self, tracker_run_id: str, **overrides) -> dict:
        diff = {
            "topic_id": "topic:alpha",
            "topic_name": "Alpha",
            "change_type": "new",
            "curr_rank": 1,
            "magnitude": 42.0,
            "details": {"source": "unit-test"},
        }
        diff.update(overrides)
        self.store.record_diffs(
            tracker_run_id=tracker_run_id,
            curr_radar_run_id="run_curr_0001",
            workspace_id=WORKSPACE_ID,
            prev_radar_run_id=None,
            diffs=[diff],
            detected_at=T1,
        )
        # 按 topic_id 精确取回该条 diff（magnitude 可能与他人相同，不能取 [0]）。
        for row in self.store.list_diffs(tracker_run_id=tracker_run_id):
            if row["topic_id"] == diff["topic_id"]:
                return row
        raise AssertionError(f"diff not persisted: {diff['topic_id']}")


class TestSchema(TrackerStoreTestBase):
    def test_schema_creates_v2_tables_only(self):
        tables = self.store.table_names()
        for expected in (
            "tracker_schedules",
            "tracker_runs",
            "tracker_diffs",
            "tracker_alerts",
        ):
            self.assertIn(expected, tables)

    def test_init_schema_is_idempotent(self):
        # 重复建表不应抛异常，也不应改变表结构。
        before = self.store.table_names()
        self.store._init_schema()
        self.store._init_schema()
        self.assertEqual(before, self.store.table_names())

    def test_does_not_create_v1_tables(self):
        # V2 迁移绝不能顺手把 V1 的表建出来（那是 V1 的职责）。
        tables = set(self.store.table_names())
        self.assertNotIn("radar_runs", tables)
        self.assertNotIn("opportunity_snapshots", tables)
        self.assertNotIn("radar_evidence", tables)

    def test_wal_mode_preserved(self):
        mode = self.store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual("wal", str(mode).lower())


class TestSchedules(TrackerStoreTestBase):
    def test_create_schedule_generates_deterministic_id(self):
        first = self._make_schedule()
        # 另起一个 store 实例，相同输入必须得到相同 ID。
        other = TrackerStore(self.db_path)
        try:
            second = other.create_schedule(WORKSPACE_ID, next_due_at=PAST)
        finally:
            other.close()
        self.assertEqual(first["schedule_id"], second["schedule_id"])
        self.assertEqual(DEFAULT_CRON, first["cron_expr"])

    def test_create_schedule_is_idempotent(self):
        self._make_schedule()
        self._make_schedule()
        self.assertEqual(1, len(self.store.list_schedules(workspace_id=WORKSPACE_ID)))

    def test_create_schedule_rejects_blank_inputs(self):
        with self.assertRaises(ValueError):
            self.store.create_schedule("")
        with self.assertRaises(ValueError):
            self.store.create_schedule(WORKSPACE_ID, cron_expr="   ")

    def test_due_schedules_returns_only_enabled_and_past_due(self):
        due = self.store.create_schedule(
            WORKSPACE_ID, cron_expr="0 * * * *", next_due_at=PAST
        )
        future = self.store.create_schedule(
            WORKSPACE_ID, cron_expr="0 0 * * *", next_due_at=FUTURE
        )
        disabled = self.store.create_schedule(
            WORKSPACE_ID, cron_expr="30 * * * *", next_due_at=PAST
        )
        self.store.set_schedule_enabled(disabled["schedule_id"], False)

        due_ids = [row["schedule_id"] for row in self.store.due_schedules(now=T1)]
        self.assertEqual([due["schedule_id"]], due_ids)
        self.assertNotIn(future["schedule_id"], due_ids)
        self.assertNotIn(disabled["schedule_id"], due_ids)

    def test_mark_schedule_ran_advances_watermark(self):
        schedule = self._make_schedule()
        updated = self.store.mark_schedule_ran(
            schedule["schedule_id"], last_run_at=T1, next_due_at=FUTURE
        )
        self.assertEqual(T1, updated["last_run_at"])
        self.assertEqual(FUTURE, updated["next_due_at"])
        # 水位推进后不再到期。
        self.assertEqual([], self.store.due_schedules(now=T2))

    def test_unknown_schedule_raises(self):
        with self.assertRaises(KeyError):
            self.store.get_schedule("schedule_missing")
        with self.assertRaises(KeyError):
            self.store.set_schedule_enabled("schedule_missing", False)
        with self.assertRaises(KeyError):
            self.store.mark_schedule_ran("schedule_missing", last_run_at=T0, next_due_at=T1)


class TestRuns(TrackerStoreTestBase):
    def test_start_run_creates_tracker_run_record(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        self.assertEqual(RUN_STATUS_RUNNING, run["status"])
        self.assertIsNone(run["finished_at"])
        self.assertIsNone(run["radar_run_id"])
        self.assertEqual(WORKSPACE_ID, run["workspace_id"])

    def test_start_run_is_idempotent_on_same_schedule_and_time(self):
        schedule = self._make_schedule()
        first = self._make_run(schedule["schedule_id"])
        second = self._make_run(schedule["schedule_id"])
        self.assertEqual(first["tracker_run_id"], second["tracker_run_id"])
        self.assertEqual(1, len(self.store.list_runs(workspace_id=WORKSPACE_ID)))

    def test_complete_run_sets_finished_at_and_status(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        done = self.store.complete_run(run["tracker_run_id"], radar_run_id="run_v1_0001")
        self.assertEqual(RUN_STATUS_COMPLETED, done["status"])
        self.assertEqual("run_v1_0001", done["radar_run_id"])
        self.assertIsNotNone(done["finished_at"])
        self.assertIsNone(done["error_message"])

    def test_fail_run_records_error_message(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        failed = self.store.fail_run(
            run["tracker_run_id"], error_message="collector timeout"
        )
        self.assertEqual(RUN_STATUS_FAILED, failed["status"])
        self.assertEqual("collector timeout", failed["error_message"])
        self.assertIsNotNone(failed["finished_at"])

    def test_fail_run_accepts_structured_error(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        failed = self.store.fail_run(
            run["tracker_run_id"], error_message={"kind": "provider_error", "code": 503}
        )
        self.assertIn("provider_error", failed["error_message"])

    def test_last_completed_radar_run_only_counts_success(self):
        schedule = self._make_schedule()
        self.assertIsNone(self.store.last_completed_radar_run(WORKSPACE_ID))

        failed = self.store.start_run(
            schedule_id=schedule["schedule_id"],
            workspace_id=WORKSPACE_ID,
            scheduled_at=T0,
        )
        self.store.fail_run(failed["tracker_run_id"], error_message="boom")
        self.assertIsNone(self.store.last_completed_radar_run(WORKSPACE_ID))

        ok = self.store.start_run(
            schedule_id=schedule["schedule_id"],
            workspace_id=WORKSPACE_ID,
            scheduled_at=T1,
        )
        self.store.complete_run(ok["tracker_run_id"], radar_run_id="run_v1_ok")
        self.assertEqual("run_v1_ok", self.store.last_completed_radar_run(WORKSPACE_ID))

    def test_unknown_run_raises(self):
        with self.assertRaises(KeyError):
            self.store.complete_run("tracker_run_missing", radar_run_id="x")
        with self.assertRaises(KeyError):
            self.store.fail_run("tracker_run_missing", error_message="x")


class TestDiffs(TrackerStoreTestBase):
    def test_record_diffs_is_idempotent(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        diff = {
            "topic_id": "topic:alpha",
            "topic_name": "Alpha",
            "change_type": "new",
            "magnitude": 12.5,
        }
        kwargs = dict(
            tracker_run_id=run["tracker_run_id"],
            curr_radar_run_id="run_curr_0001",
            workspace_id=WORKSPACE_ID,
            prev_radar_run_id=None,
            diffs=[diff],
        )
        self.assertEqual(1, self.store.record_diffs(**kwargs))
        # 同一 diff 重复写入应被 ON CONFLICT 吞掉，返回 0。
        self.assertEqual(0, self.store.record_diffs(**kwargs))
        self.assertEqual(1, len(self.store.list_diffs(tracker_run_id=run["tracker_run_id"])))

    def test_deterministic_diff_ids(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        first = self._make_diff(run["tracker_run_id"])
        diff_id = first["diff_id"]
        # 直接按同一规则重算，必须一致（D3 修复后 diff_id 包含 workspace_id）。
        from core import stable_id

        self.assertEqual(
            stable_id("tracker-diff", WORKSPACE_ID, "", "run_curr_0001",
                       "topic:alpha", "new"),
            diff_id,
        )

    def test_list_diffs_sorted_by_magnitude_desc(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        self.store.record_diffs(
            tracker_run_id=run["tracker_run_id"],
            curr_radar_run_id="run_curr_0001",
            workspace_id=WORKSPACE_ID,
            diffs=[
                {"topic_id": "topic:low", "change_type": "score_up", "magnitude": 3.0},
                {"topic_id": "topic:high", "change_type": "new", "magnitude": 90.0},
                {"topic_id": "topic:mid", "change_type": "rank_up", "magnitude": 20.0},
            ],
            detected_at=T1,
        )
        ordered = [row["topic_id"] for row in self.store.list_diffs(
            tracker_run_id=run["tracker_run_id"]
        )]
        self.assertEqual(["topic:high", "topic:mid", "topic:low"], ordered)

    def test_diff_requires_topic_and_change_type(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        with self.assertRaises(ValueError):
            self.store.record_diffs(
                tracker_run_id=run["tracker_run_id"],
                curr_radar_run_id="run_curr_0001",
                workspace_id=WORKSPACE_ID,
                diffs=[{"change_type": "new"}],
            )

    def test_details_json_round_trip(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        # 默认 details 为 {"source": "unit-test"}：验证原样 JSON 往返。
        default_row = self._make_diff(run["tracker_run_id"])
        self.assertIn("unit-test", default_row["details_json"])

        # 覆盖后的 details（含非 ASCII）必须原样保留，不被转义成 \uXXXX。
        custom_row = self._make_diff(
            run["tracker_run_id"],
            topic_id="topic:beta",
            details={"n": 1, "中文": "值"},
        )
        self.assertIn("中文", custom_row["details_json"])
        self.assertNotIn("\\u4e2d", custom_row["details_json"])


class TestAlerts(TrackerStoreTestBase):
    def test_create_alerts_is_idempotent(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        diff = self._make_diff(run["tracker_run_id"], magnitude=95.0)
        payload = [{
            "diff_id": diff["diff_id"],
            "alert_type": "high_magnitude_change",
            "severity": "critical",
            "title": "新机会出现",
            "summary": "Alpha 首次进入前五",
        }]
        kwargs = dict(
            tracker_run_id=run["tracker_run_id"],
            workspace_id=WORKSPACE_ID,
            alerts=payload,
        )
        self.assertEqual(1, self.store.create_alerts(**kwargs))
        self.assertEqual(0, self.store.create_alerts(**kwargs))
        self.assertEqual(1, len(self.store.list_alerts(workspace_id=WORKSPACE_ID)))

    def test_rejects_invalid_severity_and_missing_fields(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        base = dict(tracker_run_id=run["tracker_run_id"], workspace_id=WORKSPACE_ID)
        with self.assertRaises(ValueError):
            self.store.create_alerts(
                alerts=[{"diff_id": "d1", "alert_type": "t", "severity": "urgent"}],
                **base,
            )
        with self.assertRaises(ValueError):
            self.store.create_alerts(alerts=[{"alert_type": "t"}], **base)

    def test_delivery_flag_and_filter(self):
        schedule = self._make_schedule()
        run = self._make_run(schedule["schedule_id"])
        diff = self._make_diff(run["tracker_run_id"], magnitude=95.0)
        self.store.create_alerts(
            tracker_run_id=run["tracker_run_id"],
            workspace_id=WORKSPACE_ID,
            alerts=[{
                "diff_id": diff["diff_id"],
                "alert_type": "high_magnitude_change",
                "title": "t",
                "summary": "s",
            }],
        )
        pending = self.store.list_alerts(workspace_id=WORKSPACE_ID, undelivered_only=True)
        self.assertEqual(1, len(pending))
        self.assertEqual(0, pending[0]["delivered"])

        delivered = self.store.mark_alert_delivered(pending[0]["alert_id"])
        self.assertEqual(1, delivered["delivered"])
        self.assertIsNotNone(delivered["delivered_at"])
        self.assertEqual(
            [], self.store.list_alerts(workspace_id=WORKSPACE_ID, undelivered_only=True)
        )

    def test_unknown_alert_raises(self):
        with self.assertRaises(KeyError):
            self.store.mark_alert_delivered("alert_missing")


class TestReadOnlyGuard(TrackerStoreTestBase):
    def test_read_only_store_rejects_initialize(self):
        with self.assertRaises(ValueError):
            TrackerStore(self.db_path, read_only=True, initialize=True)

    def test_read_only_store_can_query(self):
        schedule = self._make_schedule()
        ro = TrackerStore(self.db_path, initialize=False, read_only=True)
        try:
            self.assertTrue(schedule["schedule_id"])
            self.assertEqual(1, len(ro.list_schedules(workspace_id=WORKSPACE_ID)))
            with self.assertRaises(sqlite3.OperationalError):
                ro.conn.execute("INSERT INTO tracker_schedules (schedule_id) VALUES ('x')")
        finally:
            ro.close()


if __name__ == "__main__":
    unittest.main()
