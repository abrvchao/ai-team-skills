from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from bridge import BridgeConfig, DSHAdapter, create_handler
from dispatcher import Dispatcher
from protocol import TaskRequest, TaskStatus, utcnow
from registry import AgentRegistry
from store import TaskStore
from supervisor import BridgeClient


def http_json(
    port: int,
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = None,
):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    data = json.loads(raw.decode("utf-8")) if raw else None
    return response.status, data


class PullWorkerTests(unittest.TestCase):
    def make_server(self, tmp: str):
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
        return root, store, adapter, registry, server, thread

    def close_server(self, store, adapter, server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        adapter.shutdown()
        store.close()

    def register(self, port: int, label: str = "DSH local worker"):
        status, payload = http_json(
            port,
            "POST",
            "/workers/register",
            {
                "agent_id": "dsh",
                "label": label,
                "capabilities": ["implementation", "testing"],
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["worker_id"].startswith("worker_"))
        self.assertTrue(payload["token"])
        return payload

    def test_offline_submit_stays_queued_until_worker_lease_then_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                client = BridgeClient(
                    f"http://127.0.0.1:{server.server_address[1]}",
                    timeout=5,
                )
                before = {row["agent_id"]: row for row in client.agents()}
                self.assertTrue(before["dsh"]["configured"])
                self.assertTrue(before["dsh"]["queueable"])
                self.assertFalse(before["dsh"]["available"])

                submitted = client.submit(
                    agent_id="dsh",
                    title="Pull-worker task",
                    prompt="Implement and test the change.",
                    workspace=".",
                )
                self.assertEqual(submitted["status"], "queued")
                self.assertFalse(submitted["started"])
                self.assertIsNone(submitted["started_at"])
                task_id = submitted["task_id"]

                worker = self.register(server.server_address[1])
                after = {row["agent_id"]: row for row in client.agents()}
                self.assertTrue(after["dsh"]["available"])

                status, leased = http_json(
                    server.server_address[1],
                    "POST",
                    f"/workers/{worker['worker_id']}/lease",
                    {},
                    worker["token"],
                )
                self.assertEqual(status, 200)
                task = leased["task"]
                self.assertEqual(task["task_id"], task_id)
                self.assertEqual(task["status"], "acknowledged")
                self.assertFalse(task["started"])
                self.assertIsNotNone(task["acknowledged_at"])
                self.assertIsNone(task["started_at"])
                self.assertEqual(task["prompt"], "Implement and test the change.")

                status, running = http_json(
                    server.server_address[1],
                    "POST",
                    f"/tasks/{task_id}/events",
                    {"type": "status", "status": "running"},
                    worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(running["status"], "running")
                self.assertTrue(running["started"])
                self.assertIsNotNone(running["started_at"])
            finally:
                self.close_server(store, adapter, server, thread)

    def test_worker_messages_artifacts_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                client = BridgeClient(
                    f"http://127.0.0.1:{server.server_address[1]}",
                    timeout=5,
                )
                submitted = client.submit(
                    agent_id="dsh",
                    title="Artifact task",
                    prompt="Return a file.",
                    workspace=".",
                )
                task_id = submitted["task_id"]
                worker = self.register(server.server_address[1])
                http_json(
                    server.server_address[1],
                    "POST",
                    f"/workers/{worker['worker_id']}/lease",
                    {},
                    worker["token"],
                )
                http_json(
                    server.server_address[1],
                    "POST",
                    f"/tasks/{task_id}/events",
                    {"type": "status", "status": "running"},
                    worker["token"],
                )

                client.message(task_id, "Also run the test suite.")
                status, messages = http_json(
                    server.server_address[1],
                    "GET",
                    f"/tasks/{task_id}/messages",
                    token=worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    messages["messages"][-1]["content"],
                    "Also run the test suite.",
                )

                artifact_path = root / "result.txt"
                artifact_path.write_text("done\n", encoding="utf-8")
                status, _ = http_json(
                    server.server_address[1],
                    "POST",
                    f"/tasks/{task_id}/events",
                    {
                        "type": "artifact",
                        "path": str(artifact_path),
                        "kind": "file",
                        "label": "implementation result",
                    },
                    worker["token"],
                )
                self.assertEqual(status, 200)

                status, completed = http_json(
                    server.server_address[1],
                    "POST",
                    f"/tasks/{task_id}/events",
                    {"type": "status", "status": "completed"},
                    worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(completed["status"], "completed")
                self.assertTrue(completed["started"])
                self.assertEqual(len(client.artifacts(task_id)), 1)
            finally:
                self.close_server(store, adapter, server, thread)

    def test_two_workers_cannot_lease_same_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                task_id = adapter.submit(
                    TaskRequest(
                        agent_id="dsh",
                        title="Single lease",
                        prompt="Only one worker may claim this.",
                        workspace=".",
                    )
                )
                worker_a = store.register_worker(agent_id="dsh", label="a")
                worker_b = store.register_worker(agent_id="dsh", label="b")
                barrier = threading.Barrier(3)
                results = []
                lock = threading.Lock()

                def lease(worker_id):
                    barrier.wait()
                    value = store.lease_task(
                        worker_id=worker_id,
                        agent_id="dsh",
                    )
                    with lock:
                        results.append(value.task_id if value else None)

                threads = [
                    threading.Thread(target=lease, args=(worker_a["worker_id"],)),
                    threading.Thread(target=lease, args=(worker_b["worker_id"],)),
                ]
                for item in threads:
                    item.start()
                barrier.wait()
                for item in threads:
                    item.join(timeout=2)

                self.assertEqual(results.count(task_id), 1)
                self.assertEqual(results.count(None), 1)
                task = store.get_task(task_id)
                self.assertEqual(task.status, TaskStatus.ACKNOWLEDGED)
                self.assertFalse(task.started)
            finally:
                self.close_server(store, adapter, server, thread)

    def test_cancelled_task_cannot_be_leased(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                client = BridgeClient(
                    f"http://127.0.0.1:{server.server_address[1]}",
                    timeout=5,
                )
                submitted = client.submit(
                    agent_id="dsh",
                    title="Cancel before lease",
                    prompt="Should never start.",
                )
                client.cancel(submitted["task_id"])
                worker = self.register(server.server_address[1])
                status, payload = http_json(
                    server.server_address[1],
                    "POST",
                    f"/workers/{worker['worker_id']}/lease",
                    {},
                    worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertIsNone(payload["task"])
                cancelled = client.task(submitted["task_id"])
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertFalse(cancelled["started"])
            finally:
                self.close_server(store, adapter, server, thread)

    def test_worker_heartbeat_expiry_changes_availability_not_task_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                worker = store.register_worker(agent_id="dsh", label="ttl")
                current = store.get_worker(
                    worker["worker_id"],
                    now=utcnow(),
                    ttl_seconds=30,
                )
                self.assertTrue(current["online"])
                expired = store.get_worker(
                    worker["worker_id"],
                    now=utcnow() + timedelta(seconds=31),
                    ttl_seconds=30,
                )
                self.assertFalse(expired["online"])

                task_id = adapter.submit(
                    TaskRequest(
                        agent_id="dsh",
                        title="Truth survives worker expiry",
                        prompt="Remain queued.",
                    )
                )
                task = store.get_task(task_id)
                self.assertEqual(task.status, TaskStatus.QUEUED)
                self.assertFalse(task.started)
            finally:
                self.close_server(store, adapter, server, thread)

    def test_worker_token_cannot_control_another_workers_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                client = BridgeClient(
                    f"http://127.0.0.1:{server.server_address[1]}",
                    timeout=5,
                )
                task = client.submit(
                    agent_id="dsh",
                    title="Owned",
                    prompt="Worker A only.",
                )
                a = self.register(server.server_address[1], "a")
                b = self.register(server.server_address[1], "b")
                http_json(
                    server.server_address[1],
                    "POST",
                    f"/workers/{a['worker_id']}/lease",
                    {},
                    a["token"],
                )
                status, payload = http_json(
                    server.server_address[1],
                    "POST",
                    f"/tasks/{task['task_id']}/events",
                    {"type": "status", "status": "running"},
                    b["token"],
                )
                self.assertEqual(status, 403)
                self.assertEqual(payload["error"]["code"], "forbidden")
            finally:
                self.close_server(store, adapter, server, thread)


if __name__ == "__main__":
    unittest.main()
