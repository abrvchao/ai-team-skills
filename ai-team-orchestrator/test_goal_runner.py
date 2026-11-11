from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from goal_runner import GoalRunner
from product_intake import ProductContractStore


class FakeTeam:
    def __init__(self) -> None:
        self.started = []

    def plan_run(self, **kwargs):
        return {
            "version": "goal-plan-preview-v0.1",
            "goal": kwargs["goal"],
            "participants": ["dsh", "qwen"],
            "roles": [
                {"worker_id": "dsh", "role_id": "engineering_verifier", "role_label": "Engineering Verifier"},
                {"worker_id": "qwen", "role_id": "implementation_architect", "role_label": "Implementation Architect"},
            ],
            "delivery_plan": {
                "phases": [{"phase": "primary"}, {"phase": "validation"}],
                "project_due_at": "2026-10-07T18:00:00+00:00",
            },
            "worker_tasks_started": 0,
        }

    def start_run(self, **kwargs):
        self.started.append(kwargs)
        return {"run_id": "run_goal_001"}

    def get_run(self, run_id, refresh=False):
        return {
            "run_id": run_id,
            "status": "primary_running",
            "phase": "primary",
            "participants": ["dsh", "qwen"],
            "delivery_plan": {
                "phases": [{"phase": "primary"}, {"phase": "validation"}],
                "project_due_at": "2026-10-07T18:00:00+00:00",
            },
            "progress": {
                "overall_percent": 0,
                "next_action": "wait for primary workers（等待主任务执行器）",
            },
        }


class GoalRunnerTests(unittest.TestCase):
    def test_preview_creates_contract_and_plan_without_starting_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            team = FakeTeam()
            runner = GoalRunner(contracts=store, team=team)

            result = runner.submit(
                goal="做一个县域智慧城市 AI 建设方案并形成汇报材料",
                workspace=tmp,
                approved=False,
            )

            self.assertEqual(result["mode"], "preview")
            self.assertFalse(result["started"])
            self.assertEqual(result["plan"]["worker_tasks_started"], 0)
            self.assertEqual(team.started, [])
            self.assertEqual(result["contract"]["status"], "draft")
            self.assertTrue(result["plan"]["delivery_plan"]["project_due_at"])

    def test_approved_goal_starts_same_contract_and_returns_real_run_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            team = FakeTeam()
            runner = GoalRunner(contracts=store, team=team)

            result = runner.submit(
                goal="做一个县域智慧城市 AI 建设方案并形成汇报材料",
                workspace=tmp,
                approved=True,
            )

            self.assertEqual(result["mode"], "started")
            self.assertTrue(result["started"])
            self.assertEqual(result["run_id"], "run_goal_001")
            self.assertEqual(len(team.started), 1)
            self.assertTrue(result["delivery_plan"]["project_due_at"])
            self.assertEqual(result["next_action"], "wait for primary workers（等待主任务执行器）")
