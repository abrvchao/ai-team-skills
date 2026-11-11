from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from read_model import OpportunityReadStore
from test_read_api import opportunity_row, report
from tracker import TrackerStore
from tracker_runtime import RadarTrackerRuntime
from tracker_scheduler import ScanResult
from workspace import WorkspaceStore


T0 = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)


class RadarTrackerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        with WorkspaceStore(self.db) as store:
            self.workspace = store.create(
                name="Test Workspace",
                scope="AI",
                language="zh",
                country="CN",
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_creates_schema_and_schedule_without_scanning(self):
        scan = unittest.mock.Mock()
        runtime = RadarTrackerRuntime(self.db, scan=scan)

        health = runtime.initialize()

        self.assertEqual("ok", health["status"])
        self.assertEqual(1, health["counts"]["tracker_schedules"])
        scan.assert_not_called()
        with TrackerStore(self.db, initialize=False, read_only=True) as store:
            schedule = store.list_schedules(
                workspace_id=self.workspace.workspace_id
            )[0]
            self.assertEqual("0 */6 * * *", schedule["cron_expr"])
            self.assertEqual(0, schedule["enabled"])
            self.assertGreater(
                schedule["next_due_at"],
                T0.isoformat(),
            )

    def test_manual_run_persists_tracker_run_diff_and_alert(self):
        calls = []

        def scan(schedule):
            calls.append(schedule)
            return ScanResult(
                radar_run_id="radar-run-manual",
                current_items=[
                    {
                        "topic_id": "topic:one",
                        "topic_name": "Topic One",
                        "rank": 1,
                        "score": 80,
                    }
                ],
                previous_items=[
                    {
                        "topic_id": "topic:one",
                        "topic_name": "Topic One",
                        "rank": 1,
                        "score": 60,
                    }
                ],
                complete=True,
                previous_radar_run_id="radar-run-previous",
            )

        runtime = RadarTrackerRuntime(self.db, scan=scan)
        result = runtime.run_now(self.workspace.workspace_id)

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, len(calls))
        self.assertIn("previous_radar_run_id", calls[0])
        with TrackerStore(self.db, initialize=False, read_only=True) as store:
            runs = store.list_runs(workspace_id=self.workspace.workspace_id)
            self.assertEqual("completed", runs[0]["status"])
            self.assertEqual("radar-run-manual", runs[0]["radar_run_id"])
            self.assertEqual("radar-run-previous", runs[0]["prev_radar_run_id"])
            self.assertEqual(1, len(store.list_diffs(workspace_id=self.workspace.workspace_id)))
            self.assertEqual(1, len(store.list_alerts(workspace_id=self.workspace.workspace_id)))

    def test_scan_uses_only_free_native_sources_and_same_workspace_baseline(self):
        baseline = report(
            [opportunity_row("topic:one", "Topic One", 70, "e1")]
        )
        baseline["workspace_id"] = self.workspace.workspace_id
        with OpportunityReadStore(self.db) as store:
            baseline_id = store.record_discovery(
                baseline,
                generated_at=T0,
            )

        def fake_discovery(**kwargs):
            current = report(
                [opportunity_row("topic:one", "Topic One", 92, "e2")]
            )
            current["workspace_id"] = self.workspace.workspace_id
            with OpportunityReadStore(self.db) as store:
                current_id = store.record_discovery(
                    current,
                    generated_at=T1,
                )
            return {
                "read_model": {"run_id": current_id},
                "seed": {"event_count": 10},
                "candidate_count": 1,
                "scanned_count": 1,
                "scan_errors": [],
            }

        runtime = RadarTrackerRuntime(self.db)
        with patch("tracker_runtime.run_discovery", side_effect=fake_discovery) as mocked:
            result = runtime._scan_workspace(
                {
                    "workspace_id": self.workspace.workspace_id,
                    "previous_radar_run_id": None,
                }
            )

        self.assertTrue(result.complete)
        self.assertEqual(baseline_id, result.previous_radar_run_id)
        self.assertEqual(70.0, result.previous_items[0]["scores"]["opportunity"])
        self.assertEqual(92.0, result.current_items[0]["scores"]["opportunity"])
        kwargs = mocked.call_args.kwargs
        self.assertEqual("native", kwargs["seed_mode"])
        self.assertIsNone(kwargs["gsc_site_url"])
        self.assertIsNone(kwargs["google_ads_customer_id"])
        self.assertIsNone(kwargs["website"])
        self.assertFalse(kwargs["hydrate_content"])

    def test_disabled_workspace_cannot_be_manually_run(self):
        with WorkspaceStore(self.db) as store:
            store.set_enabled(self.workspace.workspace_id, False)
        runtime = RadarTrackerRuntime(
            self.db,
            scan=lambda _: self.fail("disabled workspace must not scan"),
        )
        with self.assertRaisesRegex(ValueError, "workspace_disabled"):
            runtime.run_now(self.workspace.workspace_id)


if __name__ == "__main__":
    unittest.main()
