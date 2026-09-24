from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from bridge import BridgeConfig, DSHAdapter, create_handler
from dispatcher import Dispatcher
from pull_worker import PullWorker
from registry import AgentRegistry
from store import TaskStore
from supervisor import BridgeClient


class PullWorkerDaemonTests(unittest.TestCase):
    def make_runtime(self, tmp: str):
        root = Path(tmp) / "workspace"
        root.mkdir(parents=True)
        state = Path(tmp) / "state"
        config = BridgeConfig(
            database=state / "tasks.db",
            state_dir=state,
            workspace_root=root,
            command=[],
            allow_pull_workers=True,
            worker_ttl_seconds=30,
        )
        store = TaskStore(config.database)
        adapter = DSHAdapter(config, store)
        registry = AgentRegistry.default(
            dsh_configured=True,
            dsh_available=False,
            dsh_queueable=True,
        )
        dispatcher = Dispatcher(
            registry=registry,
            adapters={"dsh": adapter},
        )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_handler(
                dispatcher=dispatcher,
                store=store,
                registry=registry,
                dsh=adapter,
                config=config,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        return root, store, adapter, server, thread, base_url

    def close_runtime(self, store, adapter, server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        adapter.shutdown()
        store.close()

    def test_registered_daemon_executes_real_subprocess_and_completes_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, server, thread, base_url = self.make_runtime(tmp)
            try:
                supervisor = BridgeClient(base_url, timeout=5)
                worker = PullWorker(
                    bridge_url=base_url,
                    agent_id="dsh",
                    label="CI DSH worker",
                    command=[
                        sys.executable,
                        "-c",
                        "print('real child process ran')",
                    ],
                    poll_seconds=0.05,
                    heartbeat_seconds=0.1,
                )
                worker.register()

                submitted = supervisor.submit(
                    agent_id="dsh",
                    title="Execute via registered worker",
                    prompt="Run the configured worker command.",
                    workspace=".",
                )
                self.assertEqual(submitted["status"], "queued")
                self.assertFalse(submitted["started"])

                result = worker.run_once()
                self.assertEqual(result["status"], "completed")
                task = supervisor.task(submitted["task_id"])
                self.assertEqual(task["status"], "completed")
                self.assertTrue(task["started"])
                self.assertIsNotNone(task["started_at"])
                self.assertTrue(task["worker_id"].startswith("worker_"))

                artifacts = supervisor.artifacts(submitted["task_id"])
                labels = {item["label"] for item in artifacts}
                self.assertIn("worker stdout", labels)
                self.assertIn("worker stderr", labels)
                stdout = next(item for item in artifacts if item["label"] == "worker stdout")
                self.assertTrue(stdout["path"].startswith("workspace/.ai-team-worker/"))
            finally:
                self.close_runtime(store, adapter, server, thread)

    def test_launch_failure_is_failed_but_never_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, server, thread, base_url = self.make_runtime(tmp)
            try:
                supervisor = BridgeClient(base_url, timeout=5)
                worker = PullWorker(
                    bridge_url=base_url,
                    agent_id="dsh",
                    label="Broken worker",
                    command=["/definitely/not/a/real/executable"],
                    poll_seconds=0.05,
                    heartbeat_seconds=0.1,
                )
                worker.register()
                submitted = supervisor.submit(
                    agent_id="dsh",
                    title="Launch failure",
                    prompt="This must not be reported as started.",
                )
                result = worker.run_once()
                self.assertEqual(result["status"], "failed")
                task = supervisor.task(submitted["task_id"])
                self.assertEqual(task["status"], "failed")
                self.assertFalse(task["started"])
                self.assertIsNone(task["started_at"])
                self.assertIn("launch failed", task["error"])
            finally:
                self.close_runtime(store, adapter, server, thread)


if __name__ == "__main__":
    unittest.main()
