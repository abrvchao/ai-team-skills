"""工作区创建、编辑与历史数据保留的本机产品回归。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from api import create_handler
from radar_product import RadarProductActions
from read_model import OpportunityReadStore


class WorkspaceActionTests(unittest.TestCase):
    def test_create_update_and_legacy_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db):
                pass
            actions = RadarProductActions(database=db)
            server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(str(db), actions=actions))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)

            def post(path, data):
                conn.request("POST", path, json.dumps(data).encode(), {"Content-Type": "application/json"})
                response = conn.getresponse()
                return response.status, json.loads(response.read())

            try:
                status, new = post("/v1/lite/workspaces/create", {
                    "name": "具身智能研究", "scope": "具身智能", "language": "zh", "country": "CN",
                })
                self.assertEqual(status, 200)
                self.assertEqual(new["name"], "具身智能研究")
                workspace_id = new["workspace_id"]
                status, changed = post("/v1/lite/workspaces/update", {
                    "workspace_id": workspace_id, "name": "机器人研究", "scope": "机器人",
                })
                self.assertEqual(status, 200)
                self.assertEqual(changed["workspace_id"], workspace_id)
                self.assertEqual(changed["scope"], "机器人")
                conn.request("GET", "/v1/workspaces")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(len(json.loads(response.read())["items"]), 1)
                conn.request("GET", "/v1/opportunities?scope=AI")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
            finally:
                conn.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
