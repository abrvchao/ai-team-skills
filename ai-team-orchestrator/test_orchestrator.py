from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from bridge import BridgeConfig, DSHAdapter, Dispatcher, create_handler
from protocol import TaskRequest, TaskStatus
from registry import AgentRegistry
from store import TaskStore
from supervisor import BridgeClient


ROOT = Path(__file__).resolve().parent


def wait_status(adapter: DSHAdapter, task_id: str, statuses: set[TaskStatus], timeout: float = 6.0):
    deadline = time.time() + timeout
    last = adapter.status(task_id)
    while time.time() < deadline:
        last = adapter.status(task_id)
        if last.status in statuses:
            return last
        time.sleep(0.03)
    raise AssertionError(f"timeout waiting for {statuses}; last={last.status}")


class OrchestratorTests(unittest.TestCase):
    def make_adapter(self, tmp: str):
        root = Path(tmp) / "workspace"
        root.mkdir(parents=True)
        state = Path(tmp) / "state"
        config = BridgeConfig(
            database=state / "tasks.db",
            state_dir=state,
            workspace_root=root,
            command=[
                sys.executable,
                str(ROOT / "fake_worker.py"),
                "{request_file}",
            ],
        )
        store = TaskStore(config.database)
        adapter = DSHAdapter(config, store)
        return root, state, store, adapter

    def test_success_requires_ack_running_clean_exit_and_returns_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, store, adapter = self.make_adapter(tmp)
            task_id = adapter.submit(
                TaskRequest(
                    agent_id="dsh",
                    title="Implement test feature",
                    prompt="Create a result artifact.",
                    workspace=".",
                )
            )
            done = wait_status(adapter, task_id, {TaskStatus.COMPLETED})
            self.assertTrue(done.started)
            self.assertIsNotNone(done.acknowledged_at)
            self.assertIsNotNone(done.started_at)
            self.assertIsNotNone(done.finished_at)
            self.assertEqual(done.exit_code, 0)

            artifacts = adapter.artifacts(task_id)
            self.assertEqual(len(artifacts), 1)
            self.assertTrue(artifacts[0].path.startswith("workspace/.orchestrator-demo/"))
            store.close()

    def test_no_ack_never_becomes_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, store, adapter = self.make_adapter(tmp)
            task_id = adapter.submit(
                TaskRequest(
                    agent_id="dsh",
                    title="No ACK worker",
                    prompt="Do not ACK.",
                    metadata={"mode": "no_ack", "sleep_seconds": 0.35},
                )
            )
            time.sleep(0.08)
            queued = adapter.status(task_id)
            self.assertEqual(queued.status, TaskStatus.QUEUED)
            self.assertFalse(queued.started)
            self.assertIsNone(queued.acknowledged_at)
            self.assertIsNone(queued.started_at)

            failed = wait_status(adapter, task_id, {TaskStatus.FAILED})
            self.assertFalse(failed.started)
            self.assertIsNone(failed.started_at)
            self.assertIn("without ACK", failed.error or "")
            store.close()

    def test_worker_cannot_claim_completion_then_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, store, adapter = self.make_adapter(tmp)
            task_id = adapter.submit(
                TaskRequest(
                    agent_id="dsh",
                    title="False completion",
                    prompt="Emit completed then fail process.",
                    metadata={"mode": "complete_nonzero"},
                )
            )
            failed = wait_status(adapter, task_id, {TaskStatus.FAILED})
            self.assertTrue(failed.started)
            self.assertEqual(failed.exit_code, 3)
            self.assertIn("code 3", failed.error or "")
            store.close()

    def test_cancel_running_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, store, adapter = self.make_adapter(tmp)
            task_id = adapter.submit(
                TaskRequest(
                    agent_id="dsh",
                    title="Long worker",
                    prompt="Sleep until cancelled.",
                    metadata={"mode": "sleep", "sleep_seconds": 5},
                )
            )
            running = wait_status(adapter, task_id, {TaskStatus.RUNNING})
            self.assertTrue(running.started)
            cancelled = adapter.cancel(task_id)
            self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
            self.assertIsNotNone(cancelled.finished_at)
            store.close()

    def test_followup_message_is_persisted_for_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, state, store, adapter = self.make_adapter(tmp)
            task_id = adapter.submit(
                TaskRequest(
                    agent_id="dsh",
                    title="Message worker",
                    prompt="Wait for follow-up.",
                    metadata={"mode": "sleep", "sleep_seconds": 1},
                )
            )
            wait_status(adapter, task_id, {TaskStatus.RUNNING})
            adapter.message(task_id, "Please also run tests.")
            messages = store.messages(task_id)
            self.assertEqual(messages[-1]["content"], "Please also run tests.")
            message_file = state / "tasks" / task_id / "messages.jsonl"
            self.assertIn("Please also run tests.", message_file.read_text(encoding="utf-8"))
            adapter.cancel(task_id)
            store.close()

    def test_workspace_escape_and_secret_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, store, adapter = self.make_adapter(tmp)
            with self.assertRaises(ValueError):
                adapter.submit(
                    TaskRequest(
                        agent_id="dsh",
                        title="Escape",
                        prompt="Bad workspace.",
                        workspace="../outside",
                    )
                )
            with self.assertRaises(ValueError):
                adapter.submit(
                    TaskRequest(
                        agent_id="dsh",
                        title="Secret",
                        prompt="Bad metadata.",
                        metadata={"headers": {"Authorization": "Bearer secret"}},
                    )
                )
            store.close()

    def test_registry_exposes_unconfigured_future_agents_truthfully(self):
        registry = AgentRegistry.default(dsh_configured=True)
        self.assertTrue(registry.get("dsh").available)
        for agent_id in ("gemini", "grok", "codex"):
            agent = registry.get(agent_id)
            self.assertFalse(agent.available)
            self.assertFalse(agent.configured)
            self.assertEqual(agent.transport, "not_configured")

    def test_http_bridge_and_supervisor_client_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, store, adapter = self.make_adapter(tmp)
            registry = AgentRegistry.default(dsh_configured=True)
            dispatcher = Dispatcher(adapter, registry)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(
                    dispatcher=dispatcher,
                    store=store,
                    registry=registry,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = BridgeClient(
                    f"http://127.0.0.1:{server.server_address[1]}",
                    timeout=5,
                )
                agents = {row["agent_id"]: row for row in client.agents()}
                self.assertTrue(agents["dsh"]["available"])
                self.assertFalse(agents["gemini"]["available"])

                submitted = client.submit(
                    agent_id="dsh",
                    title="HTTP task",
                    prompt="Return one artifact.",
                    metadata={"mode": "success"},
                )
                task_id = submitted["task_id"]

                deadline = time.time() + 6
                while time.time() < deadline:
                    view = client.view(task_id)
                    if view.status == TaskStatus.COMPLETED.value:
                        break
                    time.sleep(0.03)
                else:
                    self.fail("HTTP task did not complete")

                self.assertTrue(view.started)
                self.assertIsNotNone(view.started_at)
                self.assertEqual(len(view.artifacts), 1)

                # Placeholder agents must fail rather than pretending dispatch.
                with self.assertRaises(RuntimeError):
                    client.submit(
                        agent_id="gemini",
                        title="Unavailable",
                        prompt="Should not run.",
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                store.close()


if __name__ == "__main__":
    unittest.main()
