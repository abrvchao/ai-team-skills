"""Node Worker 费用门禁 CLI 离线联调；不访问模型或任何平台。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cost_ledger import BudgetLedger

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "cost_gate_cli.py"


class CostGateCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "budget.sqlite"

    def invoke(self, *, request_id="task-1-model-call-1", price=10, provider="synthetic"):
        result = subprocess.run(
            [sys.executable, str(CLI), "--db", str(self.path),
             "--provider", provider, "--task-id", "synthetic-task",
             "--request-id", request_id, "--currency", "USD",
             "--unit-price-micros", str(price)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
        return result.returncode, json.loads(result.stdout)

    def configure(self, cap=20):
        BudgetLedger(self.path).install_policy(
            currency="USD", total_cap_micros=cap,
            provider_caps={"synthetic": cap},
            approved_by_user=True, approval_ref="test-approval",
        )

    def test_no_policy_means_no_execution_permit(self):
        code, output = self.invoke()
        self.assertEqual(code, 3)
        self.assertIs(output["allowed"], False)
        self.assertEqual(output["reason"], "budget_not_approved")

    def test_preflight_or_unknown_price_cannot_bypass_gate(self):
        self.configure()
        code, output = self.invoke(price=0)
        self.assertEqual(code, 3)
        self.assertIs(output["allowed"], False)
        self.assertEqual(BudgetLedger(self.path).summary()["requests"], 0)

    def test_cross_process_request_id_replay_rejected(self):
        self.configure()
        first_code, first = self.invoke()
        second_code, second = self.invoke()
        self.assertEqual(first_code, 0)
        self.assertTrue(first["allowed"])
        self.assertEqual(second_code, 3)
        self.assertFalse(second["allowed"])
        self.assertEqual(second["reason"], "duplicate_request")
        self.assertEqual(BudgetLedger(self.path).summary()["requests"], 1)

    def test_process_restart_cannot_reset_global_limit(self):
        self.configure(cap=10)
        self.assertEqual(self.invoke()[0], 0)
        code, outcome = self.invoke(request_id="task-2-model-call-1")
        self.assertEqual(code, 3)
        self.assertEqual(outcome["reason"], "global_budget_exceeded")

    def test_unapproved_provider_has_no_permit(self):
        self.configure()
        code, outcome = self.invoke(provider="unapproved")
        self.assertEqual(code, 3)
        self.assertEqual(outcome["reason"], "provider_not_approved")


if __name__ == "__main__":
    unittest.main()
