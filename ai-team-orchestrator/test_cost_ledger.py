"""费用熔断离线测试：无网络、无模型、无真实消费额度。"""
from __future__ import annotations

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from cost_ledger import BudgetLedger, CostGateDenied


def _compete(path: str, request_id: str, start, results):
    """真实独立进程竞争同一份预算；等待统一起跑信号。"""
    start.wait()
    try:
        result = BudgetLedger(path).reserve(
            request_id=request_id, task_id="test-task",
            provider="synthetic", currency="USD",
            unit_price_micros=10,
        )
        results.put(("allowed", result.may_execute_once))
    except CostGateDenied as exc:
        results.put(("denied", str(exc)))


class BudgetLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget.sqlite"
        self.ledger = BudgetLedger(self.path)

    def configure(self, *, global_cap=20, provider_cap=20, currency="USD"):
        self.ledger.install_policy(
            currency=currency, total_cap_micros=global_cap,
            provider_caps={"synthetic": provider_cap},
            approved_by_user=True, approval_ref="synthetic-approval-001",
        )

    def reserve(self, request_id="req-1", **kwargs):
        return self.ledger.reserve(
            request_id=request_id, task_id="test-task", provider="synthetic",
            currency="USD", unit_price_micros=10, **kwargs,
        )

    def test_default_is_closed_and_no_auth_is_accepted(self):
        with self.assertRaisesRegex(CostGateDenied, "budget_not_approved"):
            self.reserve()
        with self.assertRaisesRegex(CostGateDenied, "approval_or_policy_invalid"):
            self.ledger.install_policy(currency="USD", total_cap_micros=999,
                                       provider_caps={"synthetic": 999},
                                       approval_ref="fake", approved_by_user=False)
        self.assertEqual(self.ledger.summary()["available_micros"], 0)

    def test_single_use_ticket_never_allows_replay(self):
        self.configure()
        a = self.reserve()
        b = self.reserve()
        self.assertTrue(a.may_execute_once)
        self.assertFalse(b.may_execute_once)
        self.assertEqual(b.state, "already_reserved")
        self.assertEqual(self.ledger.summary()["requests"], 1)
        self.assertEqual(self.ledger.summary()["reserved_micros"], 10)

    def test_request_id_collision_is_denied(self):
        self.configure()
        self.reserve()
        with self.assertRaisesRegex(CostGateDenied, "request_id_conflict"):
            self.ledger.reserve(
                request_id="req-1", task_id="another", provider="synthetic",
                currency="USD", unit_price_micros=10,
            )

    def test_restart_does_not_reset_budget_or_release_uncertain_calls(self):
        self.configure(global_cap=10)
        self.reserve()
        after_restart = BudgetLedger(self.path)
        self.assertFalse(after_restart.reserve(
            request_id="req-1", task_id="test-task", provider="synthetic",
            currency="USD", unit_price_micros=10,
        ).may_execute_once)
        with self.assertRaisesRegex(CostGateDenied, "global_budget_exceeded"):
            after_restart.reserve(
                request_id="req-2", task_id="test-task", provider="synthetic",
                currency="USD", unit_price_micros=10,
            )
        with self.assertRaisesRegex(CostGateDenied, "policy_already_installed"):
            after_restart.install_policy(
                currency="USD", total_cap_micros=999999,
                provider_caps={"synthetic": 999999},
                approved_by_user=True, approval_ref="another-approval",
            )

    def test_provider_and_global_limits_both_apply(self):
        self.configure(global_cap=100, provider_cap=10)
        self.reserve()
        with self.assertRaisesRegex(CostGateDenied, "provider_budget_exceeded"):
            self.reserve("req-2")
        self.assertEqual(self.ledger.summary()["requests"], 1)

    def test_different_workers_and_providers_share_global_cap(self):
        self.ledger.install_policy(
            currency="USD", total_cap_micros=10,
            provider_caps={"synthetic": 10, "other": 10},
            approved_by_user=True, approval_ref="test-multi-provider",
        )
        self.reserve(request_id="first-worker")
        with self.assertRaisesRegex(CostGateDenied, "global_budget_exceeded"):
            self.ledger.reserve(
                request_id="second-worker", task_id="another-task",
                provider="other", currency="USD", unit_price_micros=10,
            )
        self.assertEqual(self.ledger.summary()["requests"], 1)

    def test_unapproved_provider_and_currency_fail_closed(self):
        self.configure()
        with self.assertRaisesRegex(CostGateDenied, "provider_not_approved"):
            self.ledger.reserve(
                request_id="req-x", task_id="task", provider="grok",
                currency="USD", unit_price_micros=10,
            )
        with self.assertRaisesRegex(CostGateDenied, "currency_mismatch"):
            self.ledger.reserve(
                request_id="req-y", task_id="task", provider="synthetic",
                currency="CNY", unit_price_micros=10,
            )

    def test_unknown_or_invalid_prices_never_reserve(self):
        self.configure()
        for invalid in (None, 0, -5, "10", True, 3.14):
            with self.subTest(price=invalid), self.assertRaises(CostGateDenied):
                self.ledger.reserve(
                    request_id="new", task_id="task", provider="synthetic",
                    currency="USD", unit_price_micros=invalid,
                )
        with self.assertRaisesRegex(CostGateDenied, "cost_overflow"):
            self.ledger.reserve(
                request_id="new", task_id="task", provider="synthetic",
                currency="USD", unit_price_micros=9_000_000_000_000_000,
                units=2,
            )
        self.assertEqual(self.ledger.summary()["requests"], 0)

    def test_corrupted_db_fails_closed(self):
        self.path.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(CostGateDenied, "ledger_unavailable"):
            self.reserve()

    def test_missing_directory_fails_closed(self):
        with self.assertRaisesRegex(CostGateDenied, "ledger_directory_missing"):
            BudgetLedger(self.path.parent / "missing" / "budget.sqlite")

    def test_simulated_restart_loop_cannot_rearm_budget(self):
        self.configure(global_cap=0, provider_cap=0)
        allowed = 0
        for n in range(100):
            try:
                if BudgetLedger(self.path).reserve(
                    request_id=f"boot-{n}", task_id="readiness",
                    provider="synthetic", currency="USD", unit_price_micros=10,
                ).may_execute_once:
                    allowed += 1
            except CostGateDenied:
                pass
        self.assertEqual(allowed, 0)
        self.assertEqual(self.ledger.summary()["requests"], 0)

    def test_real_multiprocess_atomic_last_slot(self):
        self.configure(global_cap=10, provider_cap=10)
        ctx = multiprocessing.get_context("spawn")
        start, results = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=_compete,
                   args=(str(self.path), f"parallel-{i}", start, results))
                   for i in range(2)]
        try:
            for worker in workers:
                worker.start()
            start.set()
            outputs = [results.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(timeout=15)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sum(1 for result in outputs if result == ("allowed", True)), 1)
            self.assertEqual(sum(1 for result in outputs if result[0] == "denied"), 1)
            self.assertEqual(self.ledger.summary()["requests"], 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)

    def test_real_multiprocess_same_request_id_never_reexecutes(self):
        self.configure(global_cap=20, provider_cap=20)
        ctx = multiprocessing.get_context("spawn")
        start, results = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=_compete,
                   args=(str(self.path), "same-request", start, results))
                   for _ in range(2)]
        try:
            for worker in workers:
                worker.start()
            start.set()
            outputs = [results.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(timeout=15)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sorted(outputs), [("allowed", False), ("allowed", True)])
            self.assertEqual(self.ledger.summary()["reserved_micros"], 10)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
