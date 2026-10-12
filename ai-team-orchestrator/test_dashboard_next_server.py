"""Loopback-only dashboard server tests: local stub, no real Mac or Bridge."""
from __future__ import annotations

import importlib.util
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODULE = Path(__file__).resolve().parent / "dashboard-next/server.py"
spec = importlib.util.spec_from_file_location("ai_team_dashboard_next_server", MODULE)
assert spec and spec.loader
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/monitor":
            payload = json.dumps({"bridge_available": False, "runs": [], "workers": []}).encode()
        elif self.path == "/api/status":
            payload = json.dumps({"social_sources": {"status": "scheduled_not_implemented"}}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class DashboardV2ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.server = server_module.make_server(
            0, f"http://127.0.0.1:{cls.stub.server_port}",
        )
        cls.threads = [
            threading.Thread(target=cls.stub.serve_forever, daemon=True),
            threading.Thread(target=cls.server.serve_forever, daemon=True),
        ]
        for thread in cls.threads:
            thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.stub.shutdown()
        cls.server.server_close()
        cls.stub.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)

    def read(self, path: str) -> tuple[int, bytes]:
        with urlopen(self.base + path, timeout=3) as response:
            return response.status, response.read()

    def test_static_index_and_js_are_served(self) -> None:
        status, data = self.read("/")
        self.assertEqual(status, 200)
        self.assertIn(b"AI TEAM", data)
        for name in ("data.js", "app.js", "styles.css"):
            status, body = self.read("/" + name)
            self.assertEqual(status, 200)
            self.assertTrue(body)

    def test_only_read_only_real_api_paths_are_proxied(self) -> None:
        for path in ("/api/monitor", "/api/status"):
            status, data = self.read(path)
            self.assertEqual(status, 200)
            self.assertIsInstance(json.loads(data), dict)
        status, doc = self.read("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(doc)["upstream_status"], "not_checked")

    def test_non_whitelisted_paths_cannot_read_files(self) -> None:
        for path in ("/private.pem", "/../README.md", "/api/agents", "/api/tasks"):
            with self.assertRaises(HTTPError) as caught:
                self.read(path)
            self.assertEqual(caught.exception.code, 404)

    def test_post_is_refused(self) -> None:
        request = Request(self.base + "/api/status", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 405)

    def test_only_loopback_upstream_is_allowed(self) -> None:
        for target in ("https://www.example.com:8770", "http://localhost:8770",
                       "http://127.0.0.1:8770/private",
                       "http://user:password@127.0.0.1:8770"):
            with self.assertRaises(ValueError):
                server_module.make_server(0, target)

    def test_missing_upstream_returns_explicit_502_not_fake_status(self) -> None:
        # 选一个暂未使用的端口，不停止其他测试共用的 Stub 服务。
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            unavailable_port = sock.getsockname()[1]
        isolated = server_module.make_server(
            0, f"http://127.0.0.1:{unavailable_port}",
        )
        thread = threading.Thread(target=isolated.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as caught:
                urlopen(f"http://127.0.0.1:{isolated.server_port}/api/monitor", timeout=4)
            self.assertEqual(caught.exception.code, 502)
            self.assertEqual(json.loads(caught.exception.read())["error"], "upstream_unavailable")
        finally:
            isolated.shutdown()
            isolated.server_close()
            thread.join(timeout=2)



if __name__ == "__main__":
    unittest.main()
