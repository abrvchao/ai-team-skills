from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from bridge import BridgeConfig, DSHAdapter, create_handler
from dispatcher import Dispatcher
from registry import AgentRegistry
from store import TaskStore
from supervisor import BridgeClient


def http_json(port, method, path, body=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Accept": "application/json"}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw.decode("utf-8")) if raw else {}


class MultiAgentPullTests(unittest.TestCase):
    def test_gemini_registered_pull_worker_is_real_and_agent_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            state = Path(tmp) / "state"
            config = BridgeConfig(
                database=state / "tasks.db",
                state_dir=state,
                workspace_root=root,
                command=[],
                allow_pull_workers=True,
                enabled_pull_agents=("dsh", "gemini"),
                worker_ttl_seconds=30,
            )
            store = TaskStore(config.database)
            dsh = DSHAdapter(config, store)
            gemini = DSHAdapter(
                config,
                store,
                agent_id="gemini",
                allow_push=False,
            )
            registry = AgentRegistry.default(
                dsh_configured=dsh.queueable,
                dsh_available=False,
                dsh_queueable=dsh.queueable,
                configured_pull_agents={"dsh", "gemini"},
            )
            dispatcher = Dispatcher(
                registry=registry,
                adapters={"dsh": dsh, "gemini": gemini},
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(
                    dispatcher=dispatcher,
                    store=store,
                    registry=registry,
                    dsh=dsh,
                    config=config,
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]

            try:
                client = BridgeClient(f"http://127.0.0.1:{port}", timeout=5)
                agents = {row["agent_id"]: row for row in client.agents()}
                self.assertTrue(agents["gemini"]["configured"])
                self.assertTrue(agents["gemini"]["queueable"])
                self.assertFalse(agents["gemini"]["available"])
                self.assertEqual(agents["gemini"]["transport"], "registered_pull")

                status, gemini_worker = http_json(
                    port,
                    "POST",
                    "/workers/register",
                    {
                        "agent_id": "gemini",
                        "label": "Gemini CLI worker",
                        "capabilities": ["research", "long_context", "multimodal"],
                    },
                )
                self.assertEqual(status, 201)

                agents = {row["agent_id"]: row for row in client.agents()}
                self.assertTrue(agents["gemini"]["available"])

                submitted = client.submit(
                    agent_id="gemini",
                    title="Gemini research",
                    prompt="Research one source and return a markdown file.",
                )
                self.assertEqual(submitted["status"], "queued")
                self.assertFalse(submitted["started"])

                # A DSH worker must not lease Gemini work.
                status, dsh_worker = http_json(
                    port,
                    "POST",
                    "/workers/register",
                    {
                        "agent_id": "dsh",
                        "label": "DSH worker",
                        "capabilities": ["implementation"],
                    },
                )
                self.assertEqual(status, 201)
                status, dsh_lease = http_json(
                    port,
                    "POST",
                    f"/workers/{dsh_worker['worker_id']}/lease",
                    {},
                    dsh_worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertIsNone(dsh_lease["task"])

                status, lease = http_json(
                    port,
                    "POST",
                    f"/workers/{gemini_worker['worker_id']}/lease",
                    {},
                    gemini_worker["token"],
                )
                self.assertEqual(status, 200)
                task = lease["task"]
                self.assertEqual(task["task_id"], submitted["task_id"])
                self.assertEqual(task["agent_id"], "gemini")
                self.assertEqual(task["status"], "acknowledged")
                self.assertFalse(task["started"])

                status, running = http_json(
                    port,
                    "POST",
                    f"/tasks/{task['task_id']}/events",
                    {"type": "status", "status": "running"},
                    gemini_worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(running["status"], "running")
                self.assertTrue(running["started"])

                artifact = root / "gemini-result.md"
                artifact.write_text("# Gemini result\n", encoding="utf-8")
                status, _ = http_json(
                    port,
                    "POST",
                    f"/tasks/{task['task_id']}/events",
                    {
                        "type": "artifact",
                        "path": "gemini-result.md",
                        "kind": "file",
                        "label": "Gemini result",
                    },
                    gemini_worker["token"],
                )
                self.assertEqual(status, 200)

                status, completed = http_json(
                    port,
                    "POST",
                    f"/tasks/{task['task_id']}/events",
                    {"type": "status", "status": "completed"},
                    gemini_worker["token"],
                )
                self.assertEqual(status, 200)
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(len(client.artifacts(task["task_id"])), 1)

                # Disabled agents remain truthful placeholders.
                status, rejected = http_json(
                    port,
                    "POST",
                    "/workers/register",
                    {"agent_id": "grok", "label": "not enabled", "capabilities": []},
                )
                self.assertEqual(status, 503)
                self.assertEqual(rejected["error"]["code"], "unavailable")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                dsh.shutdown()
                gemini.shutdown()
                store.close()


if __name__ == "__main__":
    unittest.main()
