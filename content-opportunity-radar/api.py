"""Read-only HTTP API V1 for Content Opportunity Radar.

The API depends only on the product read model. It never imports or invokes
external data providers, so dashboard traffic cannot consume provider quota.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from read_model import OpportunityReadStore


DEFAULT_DB = ".radar/radar.db"


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


def create_handler(database: str):
    class RadarAPIHandler(BaseHTTPRequestHandler):
        server_version = "ContentOpportunityRadarAPI/1.0"

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

        def _store(self) -> OpportunityReadStore:
            return OpportunityReadStore(database)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query, keep_blank_values=False)

            try:
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
                    with self._store() as store:
                        payload = store.list_opportunities(
                            scope=scope,
                            limit=limit,
                            cursor=cursor,
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

                    with self._store() as store:
                        if len(parts) == 1:
                            item = store.get_opportunity(topic_id, scope=scope)
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
                            pack = store.research_pack(topic_id, scope=scope)
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
            except Exception as exc:
                self._error(
                    500,
                    "internal_error",
                    f"{type(exc).__name__}: {exc}",
                )

        def _read_only(self) -> None:
            self._error(
                405,
                "read_only",
                "Content Opportunity Radar API V1 is read-only",
            )

        do_POST = _read_only
        do_PUT = _read_only
        do_PATCH = _read_only
        do_DELETE = _read_only

    return RadarAPIHandler


def serve(
    *,
    database: str = DEFAULT_DB,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    server = ThreadingHTTPServer(
        (host, port),
        create_handler(database),
    )
    print(
        json.dumps(
            {
                "status": "listening",
                "host": host,
                "port": port,
                "database": database,
                "api": "/v1",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
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
