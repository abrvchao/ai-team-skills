"""Radar V2 Milestone 3：真实 V1 SQLite 表的只读数据回放验收。

使用临时文件承载真实 V1 建表与 record_discovery()，不会触碰生产库。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from read_model import OpportunityReadStore
from tracker import TrackerStore
from tracker_history import HistoricalRunReader, HistoryValidationError, ReplayScanAdapter
from tracker_scheduler import TrackerScheduler

T0 = "2026-10-10T00:00:00+00:00"
T1 = "2026-10-10T06:00:00+00:00"
T2 = "2026-10-10T12:00:00+00:00"


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "radar.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE workspaces(workspace_id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO workspaces VALUES (?)", [("ws-a",), ("ws-b",)])
        conn.commit()
        conn.close()
        self.v1 = OpportunityReadStore(self.db)
        self.tracker = TrackerStore(self.db)
        self.schedule = self.tracker.create_schedule("ws-a", next_due_at=T0)

    def tearDown(self):
        self.tracker.close()
        self.v1.close()
        self.temp.cleanup()

    def make_run(self, when, topics, *, workspace="ws-a", scope="AI",
                 scan_errors=None, scanned_count=None):
        report = {
            "workspace_id": workspace,
            "scope": scope,
            "candidate_count": len(topics),
            "scanned_count": len(topics) if scanned_count is None else scanned_count,
            "scan_errors": scan_errors or [],
            "seed": {"source_mode": "fixture"},
            "top_opportunities": [
                {"topic_id": name, "topic": name, "rank_score": score,
                 "opportunity": {"score": score}, "discovery": {"discovery_score": score}}
                for name, score in topics
            ],
        }
        return self.v1.record_discovery(report, generated_at=datetime.fromisoformat(when))

    def test_read_real_v1_run(self):
        run_id = self.make_run(T0, [("alpha", 90), ("beta", 55)])
        with HistoricalRunReader(self.db) as reader:
            snap = reader.load(run_id=run_id, workspace_id="ws-a", scope="AI")
            self.assertEqual([item["topic_id"] for item in snap.items], ["alpha", "beta"])
            self.assertEqual(snap.items[0]["scores"]["opportunity"], 90)

    def test_cross_workspace_rejected(self):
        run_id = self.make_run(T0, [("alpha", 90)], workspace="ws-b")
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaisesRegex(HistoryValidationError, "scope_or_workspace_mismatch"):
                reader.load(run_id=run_id, workspace_id="ws-a", scope="AI")

    def test_cross_scope_rejected(self):
        run_id = self.make_run(T0, [("alpha", 90)], scope="科技")
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaisesRegex(HistoryValidationError, "scope_or_workspace_mismatch"):
                reader.load(run_id=run_id, workspace_id="ws-a", scope="AI")

    def test_scan_errors_rejected(self):
        run_id = self.make_run(T0, [("alpha", 90)], scan_errors=[{"provider": "x", "error": "offline"}])
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaisesRegex(HistoryValidationError, "source_scan_errors"):
                reader.load(run_id=run_id, workspace_id="ws-a", scope="AI")

    def test_empty_snapshot_rejected(self):
        run_id = self.make_run(T0, [], scanned_count=1)
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaisesRegex(HistoryValidationError, "empty_snapshot"):
                reader.load(run_id=run_id, workspace_id="ws-a", scope="AI")

    def test_zero_scan_rejected(self):
        run_id = self.make_run(T0, [("alpha", 90)], scanned_count=0)
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaisesRegex(HistoryValidationError, "no_scanned_candidates"):
                reader.load(run_id=run_id, workspace_id="ws-a", scope="AI")

    def test_missing_run_rejected(self):
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaisesRegex(HistoryValidationError, "run_not_found"):
                reader.load(run_id="not-a-run", workspace_id="ws-a", scope="AI")

    def test_previous_and_current_run_to_persisted_diffs(self):
        first = self.make_run(T0, [("alpha", 90), ("beta", 60)])
        second = self.make_run(T1, [("alpha", 80), ("gamma", 85)])
        with HistoricalRunReader(self.db) as reader:
            initial = TrackerScheduler(
                self.tracker, ReplayScanAdapter(reader, scope="AI", curr_run_id=first)
            ).tick(now=T0)
            self.assertEqual(initial[0]["status"], "completed")
            followup = TrackerScheduler(
                self.tracker, ReplayScanAdapter(
                    reader, scope="AI", prev_run_id=first, curr_run_id=second
                )
            ).tick(now=T1)
            self.assertEqual(followup[0]["status"], "completed")
            self.assertEqual(self.tracker.last_completed_radar_run("ws-a"), second)
            types = {row["change_type"] for row in self.tracker.list_diffs(
                tracker_run_id=followup[0]["tracker_run_id"]
            )}
            self.assertEqual(types, {"new", "removed", "score_down"})

    def test_wrong_previous_run_fails_without_diff(self):
        first = self.make_run(T0, [("alpha", 90)])
        second = self.make_run(T1, [("alpha", 80)])
        wrong = self.make_run(T2, [("beta", 75)])
        with HistoricalRunReader(self.db) as reader:
            TrackerScheduler(
                self.tracker, ReplayScanAdapter(reader, scope="AI", curr_run_id=first)
            ).tick(now=T0)
            outcome = TrackerScheduler(
                self.tracker, ReplayScanAdapter(
                    reader, scope="AI", prev_run_id=wrong, curr_run_id=second
                )
            ).tick(now=T1)
            self.assertEqual(outcome[0]["status"], "failed")
            self.assertEqual(outcome[0]["error"], "scan_execution_failed")
            self.assertEqual(self.tracker.last_completed_radar_run("ws-a"), first)

    def test_reader_connection_cannot_write(self):
        self.make_run(T0, [("alpha", 80)])
        with HistoricalRunReader(self.db) as reader:
            with self.assertRaises(sqlite3.OperationalError):
                reader.store.conn.execute("DELETE FROM radar_runs")

    def test_bad_order_rejected(self):
        older = self.make_run(T0, [("alpha", 90)])
        newer = self.make_run(T1, [("beta", 80)])
        with HistoricalRunReader(self.db) as reader:
            adapter = ReplayScanAdapter(
                reader, scope="AI", prev_run_id=newer, curr_run_id=older
            )
            with self.assertRaisesRegex(HistoryValidationError, "invalid_run_order"):
                adapter(self.schedule)


if __name__ == "__main__":
    unittest.main()
