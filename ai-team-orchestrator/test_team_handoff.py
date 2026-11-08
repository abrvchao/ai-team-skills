"""自动变更检测与真实交接的本地测试：不调用任何模型/Bridge。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from team_handoff import (
    WorkerRosterMonitor, normalized_roster, prepare_handoff, update_handoffs,
)
from team_supervisor import TeamSupervisor
from test_team_supervisor import FakeBridge


class RosterMonitoringTests(unittest.TestCase):
    def test_first_observation_is_baseline_not_false_online_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = WorkerRosterMonitor(directory)
            team = [
                {"agent_id": "dsh", "configured": True, "available": True,
                 "queueable": True, "capabilities": ["implementation"]},
                {"agent_id": "qwen", "configured": True, "available": False},
            ]
            self.assertEqual(monitor.observe(team), [])
            self.assertFalse(monitor.changes_path.exists())
            self.assertEqual(monitor.observe(team), [])
            self.assertEqual(len(json.loads(monitor.state_path.read_text())["agents"]), 2)

    def test_online_offline_new_role_and_capabilities_detected_once(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = WorkerRosterMonitor(directory)
            before = [
                {"agent_id": "dsh", "configured": True, "available": True,
                 "queueable": True, "capabilities": ["code"]},
                {"agent_id": "qwen", "configured": True, "available": False},
            ]
            monitor.observe(before)
            after = [
                {"agent_id": "dsh", "configured": True, "available": False,
                 "queueable": True, "capabilities": ["code"]},
                {"agent_id": "qwen", "configured": True, "available": True},
                {"agent_id": "opencode", "configured": True, "available": True,
                 "queueable": True, "capabilities": ["review"]},
            ]
            changed = monitor.observe(after)
            self.assertEqual({x["kind"] for x in changed},
                             {"worker_offline", "worker_online", "worker_registered"})
            self.assertEqual(monitor.observe(after), [])
            updated = [dict(x) for x in after]
            updated[2]["capabilities"] = ["research", "review"]
            self.assertEqual(
                monitor.observe(updated)[0]["kind"], "worker_capabilities_changed"
            )
            self.assertEqual(len(monitor.changes_path.read_text().splitlines()), 4)

    def test_empty_bridge_response_does_not_false_offline_everyone(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = WorkerRosterMonitor(directory)
            worker = [{"agent_id": "mimo", "available": True, "configured": True}]
            monitor.observe(worker)
            self.assertEqual(monitor.observe([]), [])
            self.assertEqual(monitor.observe(worker), [])

    def test_roster_never_persists_keys_or_other_private_metadata(self):
        normalized = normalized_roster([{
            "agent_id": "workbuddy", "configured": True, "available": True,
            "queueable": True, "token": "never-store-secret",
            "api_key": "another-secret", "capabilities": ["research"],
        }])
        self.assertNotIn("never-store-secret", json.dumps(normalized))
        self.assertNotIn("another-secret", json.dumps(normalized))

    def test_roster_change_resumes_only_blocked_independent_validation_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = FakeBridge(extra_agents=["workbuddy"])
            bridge.unavailable.add("workbuddy")
            supervisor = TeamSupervisor(bridge, state_dir=root / "runs")
            # “已被阻塞”的任务永远不可抢占；只有特定缺独立验收者的关卡可尝试恢复。
            blocked = {
                "run_id": "run_waiting_validator", "status": "blocked",
                "phase": "blocked", "created_at": "2026-10-11T00:00:00Z",
                "coverage_gaps": {"missing_independent_validator": True},
            }
            other = dict(blocked, run_id="run_other_blocked",
                         coverage_gaps={"other_blocker": True})
            supervisor.runs.save(blocked)
            supervisor.runs.save(other)
            seen: list[str] = []
            supervisor.advance = lambda run_id: (seen.append(run_id) or blocked)
            # 首次读取只是建基线，不能把在线成员当成全部刚上线。
            self.assertEqual(supervisor.advance_all(), [])
            bridge.unavailable.remove("workbuddy")
            supervisor.advance_all()
            self.assertEqual(seen, ["run_waiting_validator"])
            supervisor.advance_all()
            self.assertEqual(seen, ["run_waiting_validator"])


class HandoffTests(unittest.TestCase):
    def test_reassignment_creates_auditable_manifest_and_successor_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=root / "state")
            run = supervisor.start_run(
                goal="Continue after failed DSH while retaining task history.",
                workspace=str(root / "workspace"),
                participants=["dsh", "qwen"],
            )
            previous = run["primary"]["dsh"]["task_id"]
            bridge.complete(
                run["primary"]["qwen"]["task_id"], "primary.md",
                "# Qwen original architecture baseline\n",
            )
            bridge.fail(previous, "Worker timed out after 60000ms")
            run = supervisor.advance(run["run_id"])
            handoffs = run.get("handoffs", [])
            self.assertEqual(len(handoffs), 1)
            h = handoffs[0]
            self.assertEqual(h["source_task_id"], previous)
            self.assertEqual(h["from_worker"], "dsh")
            self.assertEqual(h["to_worker"], "qwen")
            self.assertEqual(h["status"], "assigned")
            self.assertEqual(run["progress"]["handoffs"]["pending"], 1)
            self.assertTrue(Path(h["manifest"]).is_file())
            self.assertEqual(json.loads(Path(h["manifest"]).read_text())["status"], "assigned")
            task = bridge._tasks[h["new_task_id"]]
            self.assertIn("任务交接", task["prompt"])
            self.assertEqual(task["metadata"]["handoff_manifest"], h["manifest"])
            handoff_dir = Path(task["workspace"])
            self.assertIn(previous, (handoff_dir / "HANDOFF.md").read_text())
            # 原任务仍保持失败记录，不覆盖前任历史。
            self.assertEqual(bridge._tasks[previous]["status"], "failed")
            # 第二次 tick 不得重新生成交接文件或重复提交。
            run = supervisor.advance(run["run_id"])
            self.assertEqual(len(run["handoffs"]), 1)

    def test_started_does_not_mean_delivered_until_real_artifact(self):
        run = {
            "primary": {
                "dsh": {
                    "task_id": "task_new", "status": "running",
                    "started": True, "started_at": "2026-01-01T00:01:00+00:00",
                },
            },
            "handoffs": [{
                "role_owner": "dsh", "phase": "primary", "status": "assigned",
                "new_task_id": "task_new",
            }],
        }
        update_handoffs(run, artifact_ready=lambda *_: False)
        self.assertEqual(run["handoffs"][0]["status"], "in_progress")
        run["primary"]["dsh"]["status"] = "completed"
        update_handoffs(run, artifact_ready=lambda *_: False)
        self.assertNotEqual(run["handoffs"][0]["status"], "delivered")
        update_handoffs(run, artifact_ready=lambda *_: True)
        self.assertEqual(run["handoffs"][0]["status"], "delivered")
        self.assertTrue(run["handoffs"][0]["delivered_at"])

    def test_failed_successor_is_not_marked_accepted(self):
        run = {
            "primary": {"qwen": {"task_id": "task_new", "status": "failed"}},
            "handoffs": [{"phase": "primary", "role_owner": "qwen",
                          "new_task_id": "task_new", "status": "assigned"}],
        }
        update_handoffs(run, artifact_ready=lambda *_: True)
        self.assertEqual(run["handoffs"][0]["status"], "failed")

    def test_handoff_does_not_write_outside_run_root_or_duplicate_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "run"
            kwargs = dict(
                run={"run_id": "run_1", "run_root": str(base),
                     "product_contract": {"acceptance": ["test"]}},
                phase="primary", role_id="engineering_verifier",
                role_owner="dsh", from_worker="dsh", to_worker="qwen",
                old_entry={"task_id": "task_1", "status": "failed",
                           "workspace": str(root / "source")},
                reason="timeout", attempt=1,
                to_workspace=root / "other",
            )
            handoff = prepare_handoff(**kwargs)
            self.assertTrue(Path(handoff["document"]).is_relative_to(base))
            self.assertFalse((root / "other" / "HANDOFF.md").exists())
            with self.assertRaises(FileExistsError):
                prepare_handoff(**kwargs)


if __name__ == "__main__":
    unittest.main()
