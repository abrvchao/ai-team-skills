"""Content Opportunity Radar HTTP API: read-only V1 plus local-only V2 controls.

The API depends only on the product read model. It never imports or invokes
external data providers, so dashboard traffic cannot consume provider quota.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from read_model import OpportunityReadStore
from tracker import TrackerStore
from workspace import WorkspaceStore


DEFAULT_DB = ".radar/radar.db"
DASHBOARD_DIR = Path(__file__).with_name("dashboard")
STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
LOGGER = logging.getLogger("content_opportunity_radar.api")


def _int_param(
    query: dict[str, list[str]],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (query.get(name) or [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(minimum, min(maximum, value))


def create_handler(database: str, *, actions=None):
    class RadarAPIHandler(BaseHTTPRequestHandler):
        server_version = "ContentOpportunityRadarAPI/2.0"

        def _json(self, status: int, payload) -> None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str) -> None:
            self._json(
                status,
                {
                    "error": {
                        "code": code,
                        "message": message,
                    }
                },
            )

        def _request_json(self) -> dict:
            raw_length = self.headers.get("Content-Length") or "0"
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0 or length > 64 * 1024:
                raise ValueError("request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must be a JSON object") from exc
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _local_action_allowed(self) -> bool:
            """只允许 loopback 客户端的同源 JSON 写入，阻断跨站简单请求。"""
            def loopback_host(value: str | None) -> bool:
                if not value:
                    return False
                if value.lower() == "localhost":
                    return True
                try:
                    return ipaddress.ip_address(value).is_loopback
                except ValueError:
                    return False

            try:
                if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    return False

                # 中文说明：Host 也必须解析为 loopback，降低 DNS rebinding 风险。
                host_header = (self.headers.get("Host") or "").strip()
                parsed_host = urlsplit("//" + host_header)
                if not loopback_host(parsed_host.hostname):
                    return False
                host_port = parsed_host.port or 80
                if host_port != int(self.server.server_address[1]):
                    return False

                # HTML form / text/plain / form-urlencoded 等简单请求不能触发本机写接口。
                if self.headers.get_content_type().lower() != "application/json":
                    return False

                fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
                if fetch_site and fetch_site not in {"same-origin", "none"}:
                    return False

                # 浏览器 Origin 是 serialized origin：仅允许 HTTP loopback，且不得带 path/query。
                # CLI 可不发送 Origin，但必须发送 application/json。
                origin = self.headers.get("Origin")
                if origin:
                    parsed_origin = urlsplit(origin)
                    if parsed_origin.scheme != "http":
                        return False
                    if parsed_origin.path:
                        return False
                    if parsed_origin.query or parsed_origin.fragment:
                        return False
                    if parsed_origin.username or parsed_origin.password:
                        return False
                    if not loopback_host(parsed_origin.hostname):
                        return False
                    if parsed_origin.netloc.lower() != host_header.lower():
                        return False
                return True
            except (TypeError, ValueError):
                return False

        def _static(self, route: str) -> bool:
            target = STATIC_ROUTES.get(route)
            if target is None:
                return False
            filename, content_type = target
            path = DASHBOARD_DIR / filename
            try:
                body = path.read_bytes()
            except OSError:
                self._error(404, "not_found", "dashboard asset not found")
                return True

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)
            return True

        def _store(self) -> OpportunityReadStore:
            return OpportunityReadStore(
                database,
                initialize=False,
                read_only=True,
            )

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query, keep_blank_values=False)

            try:
                if self._static(path):
                    return

                if path == "/v2":
                    self._json(
                        200,
                        {
                            "api": "radar-v2",
                            "status": "available",
                            "endpoints": [
                                "/v2/health",
                                "/v2/workspaces",
                                "/v2/workspaces/{workspace_id}/schedules",
                                "/v2/workspaces/{workspace_id}/runs",
                                "/v2/workspaces/{workspace_id}/diffs",
                                "/v2/workspaces/{workspace_id}/alerts",
                                "POST /v2/workspaces/{workspace_id}/schedule",
                                "POST /v2/workspaces/{workspace_id}/run",
                                "POST /v2/tick",
                            ],
                        },
                    )
                    return

                if path == "/v2/health":
                    required = {
                        "tracker_schedules",
                        "tracker_runs",
                        "tracker_diffs",
                        "tracker_alerts",
                    }
                    try:
                        with TrackerStore(
                            database, initialize=False, read_only=True
                        ) as tracker:
                            present = set(tracker.table_names())
                            missing = sorted(required - present)
                            if missing:
                                self._json(
                                    503,
                                    {
                                        "status": "not_initialized",
                                        "missing_tables": missing,
                                    },
                                )
                                return
                            counts = {
                                table: int(
                                    tracker.conn.execute(
                                        f"SELECT COUNT(*) FROM {table}"
                                    ).fetchone()[0]
                                )
                                for table in sorted(required)
                            }
                        with WorkspaceStore(
                            database, initialize=False, read_only=True
                        ) as workspaces:
                            workspace_count = len(
                                workspaces.list(include_disabled=True)
                            )
                        tracker_runtime = getattr(actions, "tracker_runtime", None)
                        self._json(
                            200,
                            {
                                "status": "ok",
                                "schema": "radar-v2",
                                "workspace_count": workspace_count,
                                "scheduler_running": bool(
                                    getattr(tracker_runtime, "running", False)
                                ),
                                "counts": counts,
                            },
                        )
                    except sqlite3.OperationalError:
                        self._json(
                            503,
                            {"status": "not_initialized", "missing_tables": ["workspaces"]},
                        )
                    return

                if path == "/v2/workspaces":
                    with WorkspaceStore(
                        database, initialize=False, read_only=True
                    ) as workspaces:
                        items = [
                            {
                                "workspace_id": item.workspace_id,
                                "name": item.name,
                                "scope": item.scope,
                                "language": item.language,
                                "country": item.country,
                                "enabled": item.enabled,
                            }
                            for item in workspaces.list(include_disabled=True)
                        ]
                    self._json(200, {"items": items})
                    return

                if path.startswith("/v2/workspaces/"):
                    suffix = path[len("/v2/workspaces/"):]
                    parts = suffix.split("/")
                    workspace_id = unquote(parts[0]).strip()
                    if not workspace_id:
                        self._error(400, "invalid_workspace", "workspace_id is required")
                        return
                    try:
                        with WorkspaceStore(
                            database, initialize=False, read_only=True
                        ) as workspaces:
                            workspace = workspaces.get(workspace_id)
                    except KeyError:
                        self._error(404, "not_found", "workspace not found")
                        return
                    if len(parts) == 1:
                        self._json(
                            200,
                            {
                                "workspace_id": workspace.workspace_id,
                                "name": workspace.name,
                                "scope": workspace.scope,
                                "language": workspace.language,
                                "country": workspace.country,
                                "enabled": workspace.enabled,
                            },
                        )
                        return
                    if len(parts) != 2:
                        self._error(404, "not_found", "endpoint not found")
                        return
                    resource = parts[1]
                    limit = _int_param(
                        query, "limit", 50, minimum=1, maximum=200
                    )
                    with TrackerStore(
                        database, initialize=False, read_only=True
                    ) as tracker:
                        required = {
                            "tracker_schedules",
                            "tracker_runs",
                            "tracker_diffs",
                            "tracker_alerts",
                        }
                        if not required.issubset(set(tracker.table_names())):
                            self._json(
                                503,
                                {"status": "not_initialized", "missing_tables": sorted(required - set(tracker.table_names()))},
                            )
                            return
                        if resource == "schedules":
                            items = tracker.list_schedules(
                                workspace_id=workspace_id
                            )
                        elif resource == "runs":
                            items = tracker.list_runs(
                                workspace_id=workspace_id, limit=limit
                            )
                        elif resource == "diffs":
                            items = tracker.list_diffs(
                                workspace_id=workspace_id, limit=limit
                            )
                        elif resource == "alerts":
                            undelivered = (
                                (query.get("undelivered_only") or ["false"])[0].lower()
                                in {"1", "true", "yes"}
                            )
                            items = tracker.list_alerts(
                                workspace_id=workspace_id,
                                undelivered_only=undelivered,
                                limit=limit,
                            )
                        else:
                            self._error(404, "not_found", "endpoint not found")
                            return
                    self._json(200, {"workspace_id": workspace_id, "items": items})
                    return

                if path == "/v1/health":
                    with self._store() as store:
                        self._json(200, store.health())
                    return

                if path == "/v1/providers":
                    with self._store() as store:
                        self._json(
                            200,
                            {
                                "items": store.provider_health(),
                            },
                        )
                    return

                if path == "/v1/workspaces":
                    try:
                        with WorkspaceStore(
                            database,
                            initialize=False,
                            read_only=True,
                        ) as workspace_store:
                            items = [
                                item.to_dict()
                                for item in workspace_store.list(include_disabled=False)
                            ]
                    except sqlite3.OperationalError:
                        items = []
                    self._json(200, {"items": items})
                    return

                if path.startswith("/v1/workspaces/"):
                    suffix = path[len("/v1/workspaces/"):]
                    parts = suffix.split("/")
                    workspace_id = unquote(parts[0]).strip()
                    if not workspace_id:
                        self._error(400, "invalid_workspace", "workspace_id is required")
                        return
                    try:
                        with WorkspaceStore(
                            database,
                            initialize=False,
                            read_only=True,
                        ) as workspace_store:
                            workspace = workspace_store.get(workspace_id)
                    except KeyError:
                        self._error(404, "not_found", "workspace not found")
                        return
                    except sqlite3.OperationalError:
                        self._error(404, "not_found", "workspace not found")
                        return

                    if len(parts) == 1:
                        self._json(200, workspace.to_dict())
                        return

                    if len(parts) == 2 and parts[1] == "opportunities":
                        limit = _int_param(
                            query,
                            "limit",
                            20,
                            minimum=1,
                            maximum=100,
                        )
                        cursor = (query.get("cursor") or [None])[0]
                        with self._store() as store:
                            payload = store.list_opportunities(
                                scope=workspace.scope,
                                limit=limit,
                                cursor=cursor,
                                workspace_id=workspace.workspace_id,
                            )
                        self._json(200, payload)
                        return

                    self._error(404, "not_found", "endpoint not found")
                    return

                if path == "/v1/opportunities":
                    scope = (query.get("scope") or ["AI"])[0].strip() or "AI"
                    limit = _int_param(
                        query,
                        "limit",
                        20,
                        minimum=1,
                        maximum=100,
                    )
                    cursor = (query.get("cursor") or [None])[0]
                    workspace_id = (query.get("workspace_id") or [None])[0]
                    with self._store() as store:
                        payload = store.list_opportunities(
                            scope=scope,
                            limit=limit,
                            cursor=cursor,
                            workspace_id=workspace_id,
                        )
                    self._json(200, payload)
                    return

                if path.startswith("/v1/opportunities/"):
                    suffix = path[len("/v1/opportunities/"):]
                    parts = suffix.split("/")
                    topic_id = unquote(parts[0]).strip()
                    if not topic_id:
                        self._error(400, "invalid_topic", "topic_id is required")
                        return
                    scope = (query.get("scope") or [None])[0]
                    workspace_id = (query.get("workspace_id") or [None])[0]

                    with self._store() as store:
                        if len(parts) == 1:
                            item = store.get_opportunity(
                                topic_id,
                                scope=scope,
                                workspace_id=workspace_id,
                            )
                            if item is None:
                                self._error(404, "not_found", "opportunity not found")
                                return
                            self._json(200, item)
                            return

                        if len(parts) == 2 and parts[1] == "history":
                            limit = _int_param(
                                query,
                                "limit",
                                100,
                                minimum=1,
                                maximum=365,
                            )
                            history = store.opportunity_history(
                                topic_id,
                                scope=scope,
                                workspace_id=workspace_id,
                                limit=limit,
                            )
                            if not history:
                                self._error(404, "not_found", "opportunity history not found")
                                return
                            self._json(
                                200,
                                {
                                    "topic_id": topic_id,
                                    "items": history,
                                },
                            )
                            return

                        if len(parts) == 2 and parts[1] == "research-pack":
                            pack = store.research_pack(
                                topic_id,
                                scope=scope,
                                workspace_id=workspace_id,
                            )
                            if pack is None:
                                self._error(404, "not_found", "research pack not available")
                                return
                            self._json(200, pack)
                            return

                    self._error(404, "not_found", "endpoint not found")
                    return

                if path.startswith("/v1/evidence/"):
                    evidence_id = unquote(
                        path[len("/v1/evidence/"):]
                    ).strip()
                    if not evidence_id:
                        self._error(400, "invalid_evidence", "evidence_id is required")
                        return
                    with self._store() as store:
                        item = store.get_evidence(evidence_id)
                    if item is None:
                        self._error(404, "not_found", "evidence not found")
                        return
                    self._json(200, item)
                    return

                self._error(404, "not_found", "endpoint not found")
            except ValueError as exc:
                self._error(400, "bad_request", str(exc))
            except Exception:
                LOGGER.exception("Radar API request failed")
                self._error(
                    500,
                    "internal_error",
                    "internal server error",
                )

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/v2/tick" or path.startswith("/v2/workspaces/"):
                if actions is None:
                    self._read_only()
                    return
                if not self._local_action_allowed():
                    self._error(403, "local_only", "Radar V2 actions are only available from localhost")
                    return
                try:
                    payload = self._request_json()
                    if path == "/v2/tick":
                        method_name = "tracker_tick"
                    else:
                        parts = path[len("/v2/workspaces/"):].split("/")
                        if len(parts) != 2 or not parts[0]:
                            self._error(404, "not_found", "endpoint not found")
                            return
                        workspace_id = unquote(parts[0]).strip()
                        if parts[1] == "schedule":
                            method_name = "tracker_schedule"
                        elif parts[1] == "run":
                            method_name = "tracker_run_now"
                        else:
                            self._error(404, "not_found", "endpoint not found")
                            return
                        payload["workspace_id"] = workspace_id
                    action = getattr(actions, method_name, None)
                    if not callable(action):
                        self._read_only()
                        return
                    self._json(200, action(payload))
                except KeyError:
                    self._error(404, "not_found", "workspace or schedule not found")
                except ValueError as exc:
                    self._error(400, "bad_request", str(exc))
                except Exception:
                    LOGGER.exception("Radar V2 action failed")
                    self._error(500, "internal_error", "Radar V2 action failed")
                return

            action_paths = {
                "/v1/lite/scan": "run_scan",
                "/v1/lite/handoff": "create_handoff",
                "/v1/lite/translate": "translate",
                "/v1/lite/workspaces/create": "create_workspace",
                "/v1/lite/workspaces/update": "update_workspace",
            }
            action_name = action_paths.get(path)
            if actions is None or action_name is None:
                self._read_only()
                return
            if not self._local_action_allowed():
                self._error(403, "local_only", "Radar Lite actions are only available from localhost")
                return
            try:
                payload = self._request_json()
                result = getattr(actions, action_name)(payload)
                self._json(200, result)
            except ValueError as exc:
                self._error(400, "bad_request", str(exc))
            except Exception:
                LOGGER.exception("Radar Lite action failed")
                self._error(500, "internal_error", "Radar Lite action failed")

        def _read_only(self) -> None:
            self._error(
                405,
                "read_only",
                "This endpoint is read-only; start radar_product.py for Radar Lite actions",
            )

        do_PUT = _read_only
        do_PATCH = _read_only
        do_DELETE = _read_only

    return RadarAPIHandler


def serve(
    *,
    database: str = DEFAULT_DB,
    host: str = "127.0.0.1",
    port: int = 8787,
    actions=None,
) -> None:
    server = ThreadingHTTPServer(
        (host, port),
        create_handler(database, actions=actions),
    )
    scheduler_started = False
    try:
        start_scheduler = getattr(actions, "start_background_scheduler", None)
        if callable(start_scheduler):
            start_scheduler()
            scheduler_started = True
        print(
            json.dumps(
                {
                    "status": "listening",
                    "host": host,
                    "port": port,
                    "database": database,
                    "api": ["/v1", "/v2"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if scheduler_started:
            stop_scheduler = getattr(actions, "stop_background_scheduler", None)
            if callable(stop_scheduler):
                stop_scheduler()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Content Opportunity Radar read-only API V1"
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    serve(
        database=args.db,
        host=args.host,
        port=max(1, min(args.port, 65535)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
