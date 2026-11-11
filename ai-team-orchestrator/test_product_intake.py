from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from product_intake import ProductContractStore


class FakeTeam:
    def __init__(self) -> None:
        self.calls = []

    def start_run(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run_test_001", "status": "primary_running"}


class ProductIntakeTests(unittest.TestCase):
    def test_draft_requires_approval_and_does_not_start_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            draft = store.draft(
                idea="做一个能自动比较多家 AI 模型回答质量的小产品",
                workspace="/tmp/demo-product",
                participants=["dsh", "qwen"],
            )
            self.assertEqual(draft["status"], "draft")
            self.assertTrue(draft["approval_required"])
            self.assertFalse(draft["approved"])
            self.assertIsNone(draft["run_id"])
            self.assertTrue(store.path(draft["contract_id"]).is_file())
            self.assertEqual(draft["resource_policy"]["mode"], "bounded")
            self.assertTrue(draft["resource_policy"]["stop_loss_required_for_experimental_products"])
            self.assertTrue(draft["resource_policy"]["repeat_multi_worker_run_requires_gate"])
            self.assertTrue(any("stop-loss" in item for item in draft["stop_conditions"]))

    def test_reject_never_starts_team_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            team = FakeTeam()
            draft = store.draft(idea="做一个本地文件整理助手")
            rejected = store.decide_and_start(
                contract_id=draft["contract_id"], approved=False, team=team
            )
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(team.calls, [])

    def test_explicit_approval_starts_once_and_preserves_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            team = FakeTeam()
            draft = store.draft(
                idea="做一个自动生成产品验收报告的工具",
                workspace="/tmp/report-product",
            )
            started = store.decide_and_start(
                contract_id=draft["contract_id"], approved=True, team=team
            )
            self.assertEqual(started["status"], "started")
            self.assertEqual(started["run_id"], "run_test_001")
            self.assertTrue(started["approved"])
            self.assertEqual(len(team.calls), 1)
            self.assertTrue(team.calls[0]["implementation_enabled"])
            self.assertIsNone(team.calls[0]["context_artifacts"])
            self.assertEqual(team.calls[0]["goal"], draft["idea"])

            # 中文说明：重复批准必须返回同一个 run，不得重复花费模型预算。
            again = store.decide_and_start(
                contract_id=draft["contract_id"], approved=True, team=team
            )
            self.assertEqual(again["run_id"], "run_test_001")
            self.assertEqual(len(team.calls), 1)

    def test_too_short_idea_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            with self.assertRaises(ValueError):
                store.draft(idea="AI")


if __name__ == "__main__":
    unittest.main()
