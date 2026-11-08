"""AI Team Dashboard v2: independent, loopback-only, read-only HTTP server.

中文注释：
- 新版面板独立运行，不覆盖旧版 Dashboard 进程。
- 只代理旧版 localhost Dashboard 的两个只读 API。
- 不允许外部地址、写入请求、目录遍历或外部网络服务。
"""
from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
ASSETS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/data.js": ("data.js", "application/javascript; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
PROXIED_APIS = frozenset({"/api/monitor", "/api/status"})
MAX_BODY_BYTES = 2 * 1024 * 1024


class DashboardV2Handler(BaseHTTPRequestHandler):
    server_version = "AI-Team-Dashboard-v2"
    sys_version = ""

    def log_message(self, message: str, *args: object) -> None:
        # 不记录请求原始参数、敏感 token 或上游数据。
        logging.info("dashboard-v2 http %s", str(args[1] if len(args) > 1 else "request"))

    def _send(self, code: int, data: bytes, mime: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, document: dict) -> None:
        self._send(code, json.dumps(document, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        # 固定路径白名单：查询串不会改变可访问文件列表。
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(200, {"service": "ai-team-dashboard-v2", "status": "serving",
                             "upstream_status": "not_checked"})
            return
        if path in ASSETS:
            name, mime = ASSETS[path]
            try:
                self._send(200, (ROOT / name).read_bytes(), mime)
            except OSError:
                self._json(503, {"error": "asset_unavailable"})
            return
        if path in PROXIED_APIS:
            upstream = self.server.upstream + path  # type: ignore[attr-defined]
            try:
                request = Request(upstream, headers={"Accept": "application/json"})
                with urlopen(request, timeout=3) as response:
                    if response.status != 200:
                        raise ValueError("upstream_status_not_ok")
                    payload = response.read(MAX_BODY_BYTES + 1)
                    if len(payload) > MAX_BODY_BYTES:
                        raise ValueError("upstream_payload_too_large")
                    parsed = json.loads(payload)
                    if not isinstance(parsed, dict):
                        raise ValueError("upstream_payload_not_object")
            except (OSError, URLError, HTTPError, ValueError, json.JSONDecodeError):
                self._json(502, {"error": "upstream_unavailable",
                                 "detail": "Read-only status endpoint unavailable"})
                return
            self._send(200, payload, "application/json; charset=utf-8")
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        self._json(405, {"error": "read_only"})

    def do_PUT(self) -> None:  # noqa: N802
        self._json(405, {"error": "read_only"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._json(405, {"error": "read_only"})


def make_server(port: int = 8772, upstream: str = "http://127.0.0.1:8770") -> ThreadingHTTPServer:
    """创建服务时不请求网络；只能代理本地现有 Dashboard。"""
    parts = urlsplit(upstream)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port or (
        parts.username or parts.password or parts.path not in ("", "/") or
        parts.query or parts.fragment or parts.port == port
    ):
        raise ValueError("upstream_must_be_local_legacy_dashboard")
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("invalid_listen_port")
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardV2Handler)
    server.upstream = upstream.rstrip("/")  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Team read-only Dashboard v2")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--upstream", default="http://127.0.0.1:8770")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_server(args.port, args.upstream) as service:
        print("AI Team Dashboard v2 serving on http://127.0.0.1:" + str(service.server_port), flush=True)
        service.serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()
