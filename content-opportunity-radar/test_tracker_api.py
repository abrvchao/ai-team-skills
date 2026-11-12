from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from api import create_handler
from tracker import TrackerStore
from workspace import WorkspaceStore


class FakeTrackerActions:
    def __init__(self):
        self.calls = []

    def tracker_schedule(self, payload):
        self.calls.append(("schedule", payload))
        return {"status": "scheduled", "workspace_id": payload["workspace_id"]}

    def tracker_run_now(self, payload):
        self.calls.append(("run", payload))
        return {"status": "completed", "workspace_id": payload["workspace_id"]}

    def tracker_tick(self, payload):
        self.calls.append(("tick", payload))
        return {"items": []}


class TrackerAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "radar.db"
        with WorkspaceStore(self.db) as store:
            self.workspace = store.create(
                name="API Workspace",
                scope="AI",
                language="zh",
                country="CN",
            )
        with TrackerStore(self.db) as store:
            store.create_schedule(self.workspace.workspace_id)
        self.actions = FakeTrackerActions()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_handler(str(self.db), actions=self.actions),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        status = response.status
        conn.close()
        return status, data

    def test_v2_health_and_workspace_reads(self):
        status, health = self.request("GET", "/v2/health")
        self.assertEqual(200, status)
        self.assertEqual("ok", health["status"])
        self.assertEqual("radar-v2", health["schema"])

        status, workspaces = self.request("GET", "/v2/workspaces")
        self.assertEqual(200, status)
        self.assertEqual(self.workspace.workspace_id, workspaces["items"][0]["workspace_id"])
        self.assertNotIn("gsc_site_url", workspaces["items"][0])

        status, schedules = self.request(
            "GET",
            f"/v2/workspaces/{self.workspace.workspace_id}/schedules",
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(schedules["items"]))

    def test_v2_local_write_routes_dispatch_actions(self):
        status, scheduled = self.request(
            "POST",
            f"/v2/workspaces/{self.workspace.workspace_id}/schedule",
            {"cron_expr": "0 */6 * * *"},
        )
        self.assertEqual(200, status)
        self.assertEqual("scheduled", scheduled["status"])

        status, run = self.request(
            "POST",
            f"/v2/workspaces/{self.workspace.workspace_id}/run",
            {},
        )
        self.assertEqual(200, status)
        self.assertEqual("completed", run["status"])
        self.assertEqual(["schedule", "run"], [item[0] for item in self.actions.calls])

        status, _ = self.request("POST", "/v2/tick", {})
        self.assertEqual(200, status)
        self.assertEqual("tick", self.actions.calls[-1][0])

    def test_v2_write_routes_reject_cross_site_and_simple_requests(self):
        cases = [
            {"Origin": "http://evil.example"},
            {"Content-Type": "text/plain"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Host": "evil.example"},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                status, _ = self.request("POST", "/v2/tick", {}, headers=headers)
                self.assertEqual(403, status)
        self.assertEqual([], self.actions.calls)

    def test_v2_write_route_accepts_same_origin_json(self):
        origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        status, _ = self.request(
            "POST", "/v2/tick", {}, headers={"Origin": origin}
        )
        self.assertEqual(200, status)
        self.assertEqual("tick", self.actions.calls[-1][0])

    def test_v2_health_reports_missing_schema_without_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v1-only.db"
            with WorkspaceStore(db):
                pass
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(str(db)),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=5
                )
                conn.request("GET", "/v2/health")
                response = conn.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(503, response.status)
                self.assertEqual("not_initialized", payload["status"])
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            import sqlite3

            conn = sqlite3.connect(db)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            conn.close()
            self.assertNotIn("tracker_schedules", tables)


if __name__ == "__main__":
    unittest.main()
