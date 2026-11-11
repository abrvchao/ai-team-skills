"""Radar V2 调度器验收：全部使用内存临时库，无外部 API。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tracker import TrackerStore
from tracker_scheduler import ScanResult, TrackerScheduler, next_cron_due, parse_cron

T0 = "2026-10-10T00:00:00+00:00"
T1 = "2026-10-10T06:00:00+00:00"


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.schedule = self.store.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_cron_utc(self):
        self.assertEqual(next_cron_due("0 */6 * * *", after=T0), T1)
        self.assertEqual(next_cron_due("30 8 * * *", after=T0),
                         "2026-10-10T08:30:00+00:00")

    def test_no_subhourly_execution(self):
        with self.assertRaises(ValueError):
            parse_cron("*/5 * * * *")

    def test_bad_cron_rejected(self):
        with self.assertRaises(ValueError):
            next_cron_due("0 25 * * *", after=T0)

    def test_full_run_and_no_double_dispatch(self):
        calls = []

        def run(_schedule):
            calls.append(1)
            return ScanResult(
                "radar-run-1",
                [{"topic_id": "topic1", "topic_name": "新机会", "rank": 1, "score": 80}],
                None,
                True,
            )

        scheduler = TrackerScheduler(self.store, run)
        result = scheduler.tick(now=T0)
        self.assertEqual(result[0]["status"], "completed")
        self.assertEqual(result[0]["diff_count"], 1)
        self.assertEqual(scheduler.tick(now=T0), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.store.list_diffs()), 1)
        self.assertEqual(self.store.last_completed_radar_run("ws1"), "radar-run-1")

    def test_incomplete_scan_does_not_remove_opportunities(self):
        def run(_schedule):
            return ScanResult("radar-run-2", [], None, False)

        result = TrackerScheduler(self.store, run).tick(now=T0)
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(self.store.list_diffs(), [])
        self.assertIsNone(self.store.last_completed_radar_run("ws1"))

    def test_exception_does_not_leak_token(self):
        def run(_schedule):
            raise RuntimeError("authorization: Bearer private_value")

        result = TrackerScheduler(self.store, run).tick(now=T0)
        self.assertEqual(result[0]["error"], "scan_execution_failed")
        runs = self.store.list_runs()
        self.assertNotIn("private_value", str(runs))
        self.assertEqual(runs[0]["status"], "failed")

    def test_missing_previous_snapshot_must_fail(self):
        first = TrackerScheduler(
            self.store, lambda _: ScanResult(
                "radar-run-1", [{"topic_id": "a", "rank": 1}], None, True)
        )
        self.assertEqual(first.tick(now=T0)[0]["status"], "completed")
        second = TrackerScheduler(
            self.store, lambda _: ScanResult(
                "radar-run-2", [{"topic_id": "b", "rank": 1}], None, True)
        )
        result = second.tick(now=T1)
        self.assertEqual(result[0]["error"], "previous_snapshot_missing")
        self.assertEqual(len(self.store.list_diffs()), 1)

    def test_disabled_schedule_skipped(self):
        self.store.set_schedule_enabled(self.schedule["schedule_id"], False)
        self.assertEqual(TrackerScheduler(self.store, lambda _: None).tick(now=T0), [])

    def test_requires_timezone(self):
        with self.assertRaises(ValueError):
            next_cron_due("0 0 * * *", after=datetime(2026, 10, 10))


class TestD1FailedRunCleanup(unittest.TestCase):
    """D1 回归：失败运行不得保留孤儿 diff/alert。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.schedule = self.store.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_failed_run_no_orphan_diffs(self):
        """扫描成功但 alert 持久化失败 → run=failed，无孤儿 diff。"""
        def scan_ok(_schedule):
            return ScanResult(
                "radar-run-d1", [{"topic_id": "t1", "topic_name": "T1",
                                  "rank": 1, "score": 80}],
                None, True,
            )

        scheduler = TrackerScheduler(self.store, scan_ok)
        with patch.object(TrackerStore, "create_alerts",
                          side_effect=RuntimeError("alert_db_error")):
            result = scheduler.tick(now=T0)

        self.assertEqual("failed", result[0]["status"])
        self.assertEqual(0, len(self.store.list_diffs(
            tracker_run_id=result[0]["tracker_run_id"])))
        self.assertEqual(0, len(self.store.list_alerts(workspace_id="ws1")))

    def test_failed_run_does_not_affect_other_runs(self):
        """失败运行的清理不得影响其他成功运行的数据。"""
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT OR IGNORE INTO workspaces VALUES ('ws_other')")
        conn.commit()
        conn.close()
        self.store.create_schedule("ws_other", next_due_at=T0)

        call_count = [0]

        def scan_fn(schedule):
            call_count[0] += 1
            if schedule["workspace_id"] == "ws_other":
                raise RuntimeError("scan_failed")
            return ScanResult(
                "radar-run-ok", [{"topic_id": "t1", "topic_name": "T1",
                                  "rank": 1, "score": 80}],
                None, True,
            )

        scheduler = TrackerScheduler(self.store, scan_fn)
        results = scheduler.tick(now=T0)

        ok_results = [r for r in results if r["status"] == "completed"]
        failed_results = [r for r in results if r["status"] == "failed"]
        self.assertEqual(1, len(ok_results))
        self.assertEqual(1, len(failed_results))
        self.assertEqual(1, len(self.store.list_diffs(
            tracker_run_id=ok_results[0]["tracker_run_id"])))
        self.assertEqual(0, len(self.store.list_diffs(
            tracker_run_id=failed_results[0]["tracker_run_id"])))


class TestD1AlertClosure(unittest.TestCase):
    """D4 回归：调度器到提醒的闭环测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.schedule = self.store.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_successful_tick_persists_alerts(self):
        """成功 tick 必须同时持久化 diff 和 alert。"""
        def scan_ok(_schedule):
            return ScanResult(
                "radar-run-alerts", [{"topic_id": "t1", "topic_name": "T1",
                                      "rank": 1, "score": 80}],
                None, True,
            )

        scheduler = TrackerScheduler(self.store, scan_ok)
        result = scheduler.tick(now=T0)

        self.assertEqual("completed", result[0]["status"])
        self.assertEqual(1, result[0]["diff_count"])
        self.assertEqual(1, result[0]["alert_count"])
        self.assertEqual(1, len(self.store.list_alerts(workspace_id="ws1")))

    def test_failed_scan_emits_no_alert(self):
        """扫描失败 → 无 diff、无 alert。"""
        def scan_fail(_schedule):
            return ScanResult("run-fail", [], None, False)

        scheduler = TrackerScheduler(self.store, scan_fail)
        result = scheduler.tick(now=T0)

        self.assertEqual("failed", result[0]["status"])
        self.assertEqual(0, len(self.store.list_diffs()))
        self.assertEqual(0, len(self.store.list_alerts(workspace_id="ws1")))


class TestD3SchedulerCrossWorkspace(unittest.TestCase):
    """D3 回归：调度器层面两个工作区使用相同 radar_run_id 时不得静默冲突。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws_a')")
        conn.execute("INSERT INTO workspaces VALUES ('ws_b')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.store.create_schedule("ws_a", next_due_at=T0)
        self.store.create_schedule("ws_b", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_same_radar_run_id_across_workspaces_both_succeed(self):
        """两个工作区的 scan 回调返回相同的 radar_run_id，diff/alert 必须各自独立。"""
        shared_run_id = "shared-radar-run-collision"

        def scan_fn(schedule):
            # 两个工作区返回完全相同的 radar_run_id 和快照
            return ScanResult(
                shared_run_id,
                [{"topic_id": "topic:shared", "topic_name": "共享话题",
                  "rank": 1, "score": 80}],
                None, True,
            )

        scheduler = TrackerScheduler(self.store, scan_fn)
        results = scheduler.tick(now=T0)

        completed = [r for r in results if r["status"] == "completed"]
        self.assertEqual(2, len(completed), "两个工作区都应成功完成")

        # 每个工作区各有 1 条 diff 和 1 条 alert
        for ws in ("ws_a", "ws_b"):
            diffs = self.store.list_diffs(tracker_run_id=[
                r for r in completed if self.store.get_run(
                    r["tracker_run_id"])["workspace_id"] == ws
            ][0]["tracker_run_id"])
            self.assertEqual(1, len(diffs), f"{ws} 应有 1 条 diff")
            alerts = self.store.list_alerts(workspace_id=ws)
            self.assertEqual(1, len(alerts), f"{ws} 应有 1 条 alert")

        # 两个工作区的 diff_id 必须不同
        all_diffs = self.store.list_diffs()
        self.assertEqual(2, len(all_diffs))
        self.assertNotEqual(all_diffs[0]["diff_id"], all_diffs[1]["diff_id"])


class TestD4IdempotentTick(unittest.TestCase):
    """D4 回归：重复 tick 必须幂等，不产生重复 diff/alert。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.store.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_repeated_tick_same_slot_no_duplicate(self):
        """同一调度槽位重复触发，不得产生重复 diff/alert。"""
        call_count = [0]

        def scan_fn(_schedule):
            call_count[0] += 1
            return ScanResult(
                "radar-run-idem", [{"topic_id": "t1", "topic_name": "T1",
                                    "rank": 1, "score": 80}],
                None, True,
            )

        scheduler = TrackerScheduler(self.store, scan_fn)
        # 第一次 tick
        r1 = scheduler.tick(now=T0)
        self.assertEqual(1, len(r1))
        self.assertEqual("completed", r1[0]["status"])

        # 第二次 tick（同一时间点）—— 应返回空（已被 claim）
        r2 = scheduler.tick(now=T0)
        self.assertEqual(0, len(r2))

        # scan 只被调用一次
        self.assertEqual(1, call_count[0])
        # diff 和 alert 各只有 1 条
        self.assertEqual(1, len(self.store.list_diffs()))
        self.assertEqual(1, len(self.store.list_alerts(workspace_id="ws1")))


class TestD4ConcurrentClaim(unittest.TestCase):
    """D4 回归：双调度器并发 claim 同一 schedule，仅一人有效。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        # 两个独立的 TrackerStore 连接，模拟两个进程/worker
        self.store_a = TrackerStore(self.db)
        self.store_b = TrackerStore(self.db)
        self.store_a.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store_a.close()
        self.store_b.close()
        self.tmp.cleanup()

    def test_two_schedulers_same_schedule_only_one_wins(self):
        """两个调度器同时抢占同一 schedule，恰好一个成功执行。"""
        calls_a = []
        calls_b = []

        def scan_a(_schedule):
            calls_a.append(1)
            return ScanResult(
                "radar-run-a", [{"topic_id": "t1", "topic_name": "T1",
                                 "rank": 1, "score": 80}],
                None, True,
            )

        def scan_b(_schedule):
            calls_b.append(1)
            return ScanResult(
                "radar-run-b", [{"topic_id": "t2", "topic_name": "T2",
                                 "rank": 1, "score": 70}],
                None, True,
            )

        sched_a = TrackerScheduler(self.store_a, scan_a)
        sched_b = TrackerScheduler(self.store_b, scan_b)

        # 两个调度器在同一时间点尝试 tick
        r_a = sched_a.tick(now=T0)
        r_b = sched_b.tick(now=T0)

        # 恰好一个成功、一个被跳过（claim 失败）
        total_completed = len(r_a) + len(r_b)
        self.assertEqual(1, total_completed, "并发 claim 必须恰好一人成功")
        # scan 回调只被调用一次
        self.assertEqual(1, len(calls_a) + len(calls_b))


class TestD4FailedScanZeroSideEffects(unittest.TestCase):
    """D4 回归：失败扫描不得产生任何 diff 或 alert。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.store.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_incomplete_scan_zero_diff_zero_alert(self):
        """ScanResult.complete=False → 零 diff、零 alert、run=failed。"""
        def scan_fail(_schedule):
            return ScanResult("run-fail", [], None, False)

        scheduler = TrackerScheduler(self.store, scan_fail)
        results = scheduler.tick(now=T0)

        self.assertEqual(1, len(results))
        self.assertEqual("failed", results[0]["status"])
        self.assertEqual("scan_incomplete", results[0]["error"])
        self.assertEqual(0, len(self.store.list_diffs()))
        self.assertEqual(0, len(self.store.list_alerts(workspace_id="ws1")))

    def test_scan_exception_zero_diff_zero_alert(self):
        """scan 回调抛异常 → 零 diff、零 alert、run=failed。"""
        def scan_crash(_schedule):
            raise RuntimeError("provider_timeout")

        scheduler = TrackerScheduler(self.store, scan_crash)
        results = scheduler.tick(now=T0)

        self.assertEqual(1, len(results))
        self.assertEqual("failed", results[0]["status"])
        self.assertEqual("scan_execution_failed", results[0]["error"])
        self.assertEqual(0, len(self.store.list_diffs()))
        self.assertEqual(0, len(self.store.list_alerts(workspace_id="ws1")))


class TestD4BatchFailureRollback(unittest.TestCase):
    """D4 回归：批处理失败时整个 run 的 diff/alert 必须回滚。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO workspaces VALUES ('ws1')")
        conn.commit()
        conn.close()
        self.store = TrackerStore(self.db)
        self.store.create_schedule("ws1", next_due_at=T0)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_alert_creation_failure_rolls_back_diffs_too(self):
        """create_alerts 失败时，该 run 的 diff 和 alert 全部清理，run 标记 failed。"""
        def scan_ok(_schedule):
            return ScanResult(
                "radar-run-rollback", [{"topic_id": "t1", "topic_name": "T1",
                                        "rank": 1, "score": 80}],
                None, True,
            )

        scheduler = TrackerScheduler(self.store, scan_ok)
        with patch.object(TrackerStore, "create_alerts",
                          side_effect=RuntimeError("alert_db_down")):
            results = scheduler.tick(now=T0)

        self.assertEqual("failed", results[0]["status"])
        run_id = results[0]["tracker_run_id"]
        # 失败运行不得残留 diff
        self.assertEqual(0, len(self.store.list_diffs(tracker_run_id=run_id)))
        # 失败运行不得残留 alert
        self.assertEqual(0, len(self.store.list_alerts(workspace_id="ws1")))


if __name__ == "__main__":
    unittest.main()
