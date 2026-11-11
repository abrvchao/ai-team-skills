"""P0 回放测试：独立源/目的数据库 + 实际 V1 表结构与记录。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar_v2_p0_replay import replay_history
from read_model import OpportunityReadStore
from tracker import TrackerStore


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.source = self.home / "input.db"
        with OpportunityReadStore(self.source) as source:
            for idx, data in enumerate(
                [[("alpha", 80), ("beta", 60)],
                 [("alpha", 72), ("gamma", 75)]]
            ):
                source.record_discovery({
                    "scope": "AI Agents", "workspace_id": None,
                    "candidate_count": 3, "scanned_count": 2,
                    "seed": {"source_mode": "test-existing-run"},
                    "top_opportunities": [
                        {"topic_id": topic, "topic": topic,
                         "opportunity": {"score": score},
                         "discovery": {"discovery_score": score},
                         "rank_score": score}
                        for topic, score in data
                    ],
                }, generated_at=datetime(
                    2026, 10, 9 + idx, 12, tzinfo=timezone.utc
                ))

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end_historical_replay_and_source_integrity(self):
        result = replay_history(self.source, self.home / "result")
        self.assertEqual(result["provider_api_calls"], 0)
        self.assertEqual(result["tracker_runs_completed"], 2)
        self.assertEqual(result["diff_count"], 3)
        self.assertEqual(result["change_counts"],
                         {"new": 1, "removed": 1, "score_down": 1})
        self.assertEqual(result["source_database_sha256_before"],
                         result["source_database_sha256_after"])
        self.assertEqual(json.loads(
            (self.home / "result/summary.json").read_text()
        )["diff_count"], 3)
        with TrackerStore(self.home / "result/replay.db", read_only=True,
                          initialize=False) as store:
            self.assertEqual(len(store.list_runs()), 2)
            self.assertEqual(len(store.list_diffs()), 5)  # 首轮 bootstrap 2 项

    def test_cannot_override_existing_replay(self):
        replay_history(self.source, self.home / "result")
        with self.assertRaises(FileExistsError):
            replay_history(self.source, self.home / "result")

    def test_rejects_same_input_output(self):
        same_dir = self.home / "same"
        same_dir.mkdir()
        same_file = same_dir / "replay.db"
        shutil.copyfile(self.source, same_file)
        with self.assertRaises(ValueError):
            replay_history(same_file, same_dir)

    def test_not_enough_valid_runs(self):
        with OpportunityReadStore(self.source) as store:
            store.conn.execute("DELETE FROM opportunity_snapshots WHERE "
                               "run_id = (SELECT run_id FROM radar_runs "
                               "ORDER BY generated_at DESC LIMIT 1)")
            store.conn.commit()
        with self.assertRaisesRegex(ValueError, "not_enough_valid_legacy_runs"):
            replay_history(self.source, self.home / "result")

    def test_replay_never_modifies_source_workspace(self):
        replay_history(self.source, self.home / "result")
        with sqlite3.connect(self.source) as src:
            self.assertEqual(src.execute(
                "SELECT COUNT(*) FROM radar_runs WHERE workspace_id IS NOT NULL"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
