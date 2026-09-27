"""Readiness endpoint truth tests.

Model health is observable, never authoritative: only a registered, live worker
may make the agent available, and readiness reports must be locally
authenticated.
"""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from bridge import BridgeConfig, DSHAdapter, create_handler, write_bootstrap_token
from dispatcher import Dispatcher
from registry import AgentRegistry
from store import TaskStore

BOOTSTRAP_TOKEN = "test-bootstrap-token"


def http_json(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = None
    sent = {"Accept": "application/json"}
    sent.update(headers or {})
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=sent)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    data = json.loads(raw.decode("utf-8")) if raw else None
    return response.status, data


class ReadinessTests(unittest.TestCase):
    def make_server(self, tmp: str, *, bootstrap_token: str = BOOTSTRAP_TOKEN):
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
        dispatcher = Dispatcher(registry=registry, adapters={"dsh": adapter})
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_handler(
                dispatcher=dispatcher,
                store=store,
                registry=registry,
                dsh=adapter,
                config=config,
                bootstrap_token=bootstrap_token,
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

    def dsh_agent(self, port: int) -> dict:
        status, payload = http_json(port, "GET", "/agents")
        self.assertEqual(status, 200)
        return next(item for item in payload["items"] if item["agent_id"] == "dsh")

    def test_bootstrap_token_is_written_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir(parents=True)
            state = Path(tmp) / "state"
            state.mkdir(parents=True)
            config = BridgeConfig(
                database=state / "tasks.db",
                state_dir=state,
                workspace_root=root,
                command=[],
            )
            token = write_bootstrap_token(config)
            path = state / "bootstrap-token"
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8").strip(), token)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unauthenticated_readiness_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                port = server.server_address[1]
                status, payload = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {"agent_id": "dsh", "model_available": True},
                )
                self.assertEqual(status, 403)
                self.assertEqual(payload["error"]["code"], "forbidden")
                self.assertFalse(self.dsh_agent(port)["available"])
            finally:
                self.close_server(store, adapter, server, thread)

    def test_wrong_bootstrap_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                port = server.server_address[1]
                status, _ = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {"agent_id": "dsh", "model_available": True},
                    headers={"X-Bootstrap-Token": "not-the-token"},
                )
                self.assertEqual(status, 403)
                self.assertFalse(self.dsh_agent(port)["available"])
            finally:
                self.close_server(store, adapter, server, thread)

    def test_readiness_cannot_set_availability_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                port = server.server_address[1]
                status, payload = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {"agent_id": "dsh", "available": True},
                    headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                )
                self.assertEqual(status, 400)
                self.assertIn("cannot control availability", payload["error"]["message"])
                self.assertFalse(self.dsh_agent(port)["available"])
            finally:
                self.close_server(store, adapter, server, thread)

    def test_unregistered_worker_cannot_be_made_available_by_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                port = server.server_address[1]
                status, _ = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {"agent_id": "dsh", "model_available": True},
                    headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                )
                self.assertEqual(status, 200)
                agent = self.dsh_agent(port)
                self.assertFalse(agent["available"])
                self.assertEqual(store.list_workers(agent_id="dsh"), [])
            finally:
                self.close_server(store, adapter, server, thread)

    def test_model_unavailable_reason_is_visible_while_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                port = server.server_address[1]
                status, _ = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {
                        "agent_id": "dsh",
                        "model_available": False,
                        "reason": "model_unavailable",
                    },
                    headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                )
                self.assertEqual(status, 200)
                agent = self.dsh_agent(port)
                self.assertFalse(agent["available"])
                self.assertEqual(agent["reason"], "model_unavailable")
                self.assertIn("model_unavailable", agent["note"])

                status, _ = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {"agent_id": "dsh", "model_available": True},
                    headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
                )
                self.assertEqual(status, 200)
                recovered = self.dsh_agent(port)
                self.assertFalse(recovered["available"])
                self.assertEqual(recovered["reason"], "")
            finally:
                self.close_server(store, adapter, server, thread)

    def test_registered_live_worker_makes_agent_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(tmp)
            try:
                port = server.server_address[1]
                status, worker = http_json(
                    port,
                    "POST",
                    "/workers/register",
                    {
                        "agent_id": "dsh",
                        "label": "DSH local worker",
                        "capabilities": ["implementation"],
                    },
                )
                self.assertEqual(status, 201)
                self.assertTrue(worker["token"])
                agent = self.dsh_agent(port)
                self.assertTrue(agent["available"])
                self.assertEqual(agent["reason"], "")
            finally:
                self.close_server(store, adapter, server, thread)

    def test_readiness_endpoint_disabled_without_bootstrap_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, store, adapter, registry, server, thread = self.make_server(
                tmp, bootstrap_token=""
            )
            try:
                port = server.server_address[1]
                status, _ = http_json(
                    port,
                    "POST",
                    "/workers/readiness",
                    {"agent_id": "dsh", "model_available": True},
                    headers={"X-Bootstrap-Token": "anything"},
                )
                self.assertEqual(status, 403)
                self.assertFalse(self.dsh_agent(port)["available"])
            finally:
                self.close_server(store, adapter, server, thread)


if __name__ == "__main__":
    unittest.main()
