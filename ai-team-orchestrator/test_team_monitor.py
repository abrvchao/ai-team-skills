"""总控面板子任务 / Worker 实时进度的离线回归测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from team_monitor import build_monitor, reported_progress


class FakeClient:
    def __init__(self, tasks=None, progress=None, broken=False):
        self.rows = tasks or []
        self.events = progress or {}
        self.broken = broken
        self.lookups = []

    def tasks(self):
        if self.broken:
            raise ConnectionError("bridge offline")
        return self.rows

    def progress(self, task_id):
        self.lookups.append(task_id)
        return self.events.get(task_id, [])


class MonitorTests(unittest.TestCase):
    def test_only_explicit_progress_is_shown(self):
        self.assertIsNone(reported_progress([])["percent"])
        self.assertIsNone(reported_progress([{"stage": "started"}])["percent"])
        self.assertEqual(reported_progress([{"percent": 0, "stage": "queued"}])["percent"], 0)
        self.assertEqual(reported_progress([{"percent": 45.5}, {"stage": "handoff"}])["percent"], 45.5)
        self.assertIsNone(reported_progress([{"percent": True}])["percent"])
        self.assertIsNone(reported_progress([{"percent": 180}])["percent"])

    def _run(self, root, status="blocked"):
        value = {
            "run_id": "run_monitor_01", "goal": "Radar V2 unit monitor", "status": status,
            "phase": status, "updated_at": "2026-10-11T12:00:00Z",
            "primary": {"dsh": {
                "task_id": "task_old", "worker_id": "dsh", "status": "completed",
                "started": True, "artifact": "/tmp/nonexistent.md",
            }},
            "reviews": {"qwen-reviews-dsh": {
                "task_id": "task_live", "reviewer": "qwen", "status": "running",
                "started": True,
            }},
            "synthesis": None, "implementation": None, "validation": None,
            "progress": {
                "overall_percent": 80, "blockers": ["no validator"],
                "phases": {
                    "primary": {"state": "complete", "percent": 100, "task_count": 1},
                    "review": {"state": "running", "percent": 40, "task_count": 1},
                    "validation": {"state": "blocked", "percent": 0, "task_count": 0},
                },
            },
        }
        (root / "run_monitor_01.json").write_text(json.dumps(value), encoding="utf8")

    def test_project_child_tasks_worker_status_and_real_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root)
            tasks = [
                {"task_id": "task_old", "agent_id": "dsh", "status": "completed",
                 "title": "Old task", "metadata": {"team_run_id": "run_monitor_01",
                                                       "token": "should-not-be-exposed"}},
                {"task_id": "task_live", "agent_id": "qwen", "status": "running",
                 "title": "Review code", "started": True,
                 "metadata": {"team_run_id": "run_monitor_01", "team_phase": "review",
                              "secret": "privatevalue"},
                 "created_at": "2026-10-11T12:00:00Z",
                 "prompt": "NEVER_RETURN_PRIVATE_PROMPT"},
            ]
            client = FakeClient(tasks, {"task_live": [
                {"stage": "analyzing", "percent": 30, "message": "reviewing",
                 "created_at": "2026-10-11T12:01:00Z"},
            ]})
            agents = [
                {"agent_id": "dsh", "available": True, "configured": True, "queueable": True},
                {"agent_id": "qwen", "available": True, "configured": True, "queueable": True},
                {"agent_id": "mimo", "available": False, "configured": True},
            ]
            monitor = build_monitor(client, root, agents)
            run = monitor["runs"][0]
            self.assertEqual(run["overall_percent"], 80)
            self.assertEqual(run["percent_source"], "supervisor_phase_weighting")
            self.assertEqual(run["task_count"], 2)
            self.assertEqual(run["tasks"][1]["status"], "running")
            self.assertEqual(run["tasks"][1]["progress"]["percent"], 30)
            self.assertEqual(run["tasks"][0]["progress"]["percent"], None)
            self.assertEqual(run["blockers"], ["no validator"])
            qwen = next(x for x in monitor["workers"] if x["agent_id"] == "qwen")
            self.assertTrue(qwen["busy"])
            self.assertEqual(qwen["tasks"][0]["progress"]["percent"], 30)
            mimo = next(x for x in monitor["workers"] if x["agent_id"] == "mimo")
            self.assertFalse(mimo["online"])
            self.assertFalse(mimo["busy"])
            self.assertEqual(mimo["sampled_task_count"], 0)
            self.assertEqual(set(client.lookups), {"task_live", "task_old"})
            output = json.dumps(monitor)
            self.assertNotIn("NEVER_RETURN_PRIVATE_PROMPT", output)
            self.assertNotIn("should-not-be-exposed", output)
            self.assertNotIn("privatevalue", output)

    def test_fallback_when_bridge_is_unreachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root)
            snapshot = build_monitor(FakeClient(broken=True), root, [])
            self.assertFalse(snapshot["bridge_available"])
            self.assertEqual(snapshot["runs"][0]["tasks"][1]["source"], "supervisor_snapshot")
            self.assertIsNone(snapshot["runs"][0]["tasks"][1]["progress"]["percent"])

    def test_progress_queries_have_hard_limit(self):
        tasks = [
            {"task_id": f"task_active{i}", "agent_id": "qwen", "status": "running",
             "title": "Run"} for i in range(12)
        ]
        client = FakeClient(tasks)
        with tempfile.TemporaryDirectory() as tmp:
            data = build_monitor(client, Path(tmp), [
                {"agent_id": "qwen", "available": True, "configured": True},
            ], max_progress_reads=3)
        self.assertEqual(len(client.lookups), 3)
        self.assertEqual(data["workers"][0]["active_count"], 12)
        self.assertEqual(data["task_sample_size"], 12)

    def test_no_tasks_is_not_reported_as_zero_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = build_monitor(FakeClient(), Path(tmp), [
                {"agent_id": "workbuddy", "available": True},
            ])
        worker = data["workers"][0]
        self.assertFalse(worker["busy"])
        self.assertEqual(worker["tasks"], [])
        self.assertNotIn("percent", worker)

    def test_recent_completed_task_shows_reported_percent_if_available(self):
        client = FakeClient(
            [{"task_id": "task_done", "agent_id": "workbuddy", "status": "completed",
              "title": "审查结束", "created_at": "2026-10-11T10:00:00Z"}],
            {"task_done": [{"stage": "artifact_saved", "percent": 100}]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            data = build_monitor(client, Path(tmp), [{"agent_id": "workbuddy", "available": True}])
        worker = data["workers"][0]
        self.assertFalse(worker["busy"])
        self.assertEqual(worker["tasks"][0]["progress"]["percent"], 100)

    def test_invalid_run_file_does_not_break_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run_bad.json").write_text("{bad json")
            data = build_monitor(FakeClient(), Path(tmp), [])
            self.assertEqual(data["runs"], [])


if __name__ == "__main__":
    unittest.main()
