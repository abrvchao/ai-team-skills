"""Bootstrap / Local Demo V1 for Content Opportunity Radar.

This module is orchestration only. It reuses collector.py, radar.py and api.py;
it does not duplicate provider, scoring, or read-model logic.

Examples:
    python bootstrap.py doctor
    python bootstrap.py init --scope "AI"
    python bootstrap.py collect
    python bootstrap.py radar --scope "AI"
    python bootstrap.py demo --scope "AI"
    python bootstrap.py serve
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from api import serve as serve_api
from collector import CollectionService, CollectorConfig, RadarStore
from radar import run_discovery
from read_model import OpportunityReadStore, safe_warning
from workspace import WorkspaceStore


DEFAULT_CONFIG = ".radar/local-collection-plan.json"
DEFAULT_DB = ".radar/radar.db"
OPTIONAL_ENV_VARS = (
    "GITHUB_TOKEN",
    "GSC_ACCESS_TOKEN",
    "GOOGLE_ADS_ACCESS_TOKEN",
)


def _sensitive_key(value: object) -> bool:
    normalized = str(value or "").casefold().replace("-", "_")
    return any(
        marker in normalized
        for marker in (
            "access_token",
            "refresh_token",
            "id_token",
            "authorization",
            "client_secret",
            "api_key",
            "apikey",
            "password",
            "developer_token",
            "token",
            "secret",
            "cookie",
            "private_key",
            "credential",
        )
    )


def _public(value: Any) -> Any:
    """Return a stdout-safe representation with no credential values."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _sensitive_key(key)
                else _public(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, str):
        return safe_warning(value)
    return value


def public_collection_plan(
    *,
    scope: str = "AI",
    database: str = DEFAULT_DB,
) -> dict[str, Any]:
    scope = scope.strip() or "AI"
    return {
        "database": database,
        "jobs": [
            {
                "id": "public-github",
                "provider": "github",
                "topic": scope,
                "interval_seconds": 1800,
                "limit": 30,
            },
            {
                "id": "public-hackernews",
                "provider": "hackernews",
                "topic": scope,
                "interval_seconds": 900,
                "limit": 30,
            },
            {
                "id": "public-google-news",
                "provider": "google_news",
                "topic": scope,
                "interval_seconds": 1800,
                "limit": 30,
            },
            {
                "id": "public-gdelt",
                "provider": "gdelt",
                "topic": scope,
                "interval_seconds": 1800,
                "limit": 30,
            },
        ],
    }


def write_local_config(
    *,
    path: str | Path = DEFAULT_CONFIG,
    scope: str = "AI",
    database: str = DEFAULT_DB,
    overwrite: bool = False,
) -> dict[str, Any]:
    target = Path(path)
    if target.exists() and not overwrite:
        return {
            "created": False,
            "path": str(target),
            "reason": "exists",
        }

    plan = public_collection_plan(scope=scope, database=database)
    # Validate through the real collector contract before writing anything.
    CollectorConfig.from_dict(plan)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "created": True,
        "path": str(target),
        "database": database,
        "scope": scope.strip() or "AI",
        "providers": [job["provider"] for job in plan["jobs"]],
    }


def _db_parent_writable(database: str) -> bool:
    parent = Path(database).expanduser().parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".radar-write-", delete=True):
            return True
    except OSError:
        return False


def doctor(
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    database: str = DEFAULT_DB,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    dashboard = Path(__file__).with_name("dashboard")

    sqlite_ok = True
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("SELECT 1")
        connection.close()
    except Exception:
        sqlite_ok = False

    env_status = {
        name: bool(environ.get(name))
        for name in OPTIONAL_ENV_VARS
    }
    assets = {
        name: (dashboard / name).is_file()
        for name in ("index.html", "app.js", "styles.css")
    }
    public_providers = [
        job["provider"]
        for job in public_collection_plan(database=database)["jobs"]
    ]

    checks = {
        "python_supported": sys.version_info >= (3, 11),
        "sqlite_available": sqlite_ok,
        "database_parent_writable": _db_parent_writable(database),
        "dashboard_assets_present": all(assets.values()),
    }
    return {
        "ready": all(checks.values()),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "config_path": str(config_path),
        "config_exists": Path(config_path).is_file(),
        "database": database,
        "checks": checks,
        "optional_credentials_present": env_status,
        "dashboard_assets": assets,
        "public_providers": public_providers,
    }


def prepare(
    *,
    database: str = DEFAULT_DB,
) -> dict[str, Any]:
    """Initialize/migrate every local SQLite schema without network access."""
    with RadarStore(database) as collector_store:
        collector_counts = collector_store.counts()
    with OpportunityReadStore(database) as read_store:
        read_health = read_store.health()
    with WorkspaceStore(database) as workspace_store:
        workspace_count = len(workspace_store.list())

    return {
        "database": database,
        "collector": collector_counts,
        "read_model": read_health,
        "workspaces": workspace_count,
        "network_used": False,
    }


def collect(
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    force: bool = False,
) -> dict[str, Any]:
    config = CollectorConfig.load(config_path)
    service = CollectionService(config)
    return _public(service.run_once(force=force))


def radar(
    *,
    scope: str = "AI",
    database: str = DEFAULT_DB,
    top_n: int = 5,
    seed_limit: int = 30,
    candidate_count: int = 8,
    deep_limit: int = 10,
) -> dict[str, Any]:
    base = Path(database).parent
    result = run_discovery(
        scope=scope.strip() or "AI",
        seed_limit=max(1, min(seed_limit, 50)),
        candidate_count=max(1, min(candidate_count, 30)),
        top_n=max(1, min(top_n, 20)),
        deep_limit=max(1, min(deep_limit, 30)),
        snapshot_path=str(base / "snapshots.jsonl"),
        cache_path=str(base / "provider-cache.json"),
        content_snapshot_path=str(base / "page-snapshots.jsonl"),
        collector_db=database,
        collector_since_hours=72,
        read_model_db=database,
        include_research_pack=True,
    )
    return _public(result)


def demo(
    *,
    scope: str = "AI",
    config_path: str | Path = DEFAULT_CONFIG,
    database: str = DEFAULT_DB,
    overwrite_config: bool = False,
    top_n: int = 5,
) -> dict[str, Any]:
    config_target = Path(config_path)
    init_result = None
    if not config_target.exists() or overwrite_config:
        init_result = write_local_config(
            path=config_target,
            scope=scope,
            database=database,
            overwrite=overwrite_config,
        )

    collection = collect(
        config_path=config_target,
        force=True,
    )
    radar_result = radar(
        scope=scope,
        database=database,
        top_n=top_n,
    )

    jobs = list(collection.get("jobs") or [])
    provider_status = [
        {
            "provider": row.get("provider"),
            "status": row.get("status") or row.get("reason"),
            "event_count": int(row.get("event_count") or 0),
            "warnings": list(row.get("warnings") or []),
        }
        for row in jobs
    ]

    top = list(radar_result.get("top_opportunities") or [])
    summary = [
        {
            "rank": index,
            "topic": row.get("topic"),
            "rank_score": (row.get("rank_score") or 0),
            "opportunity_score": ((row.get("opportunity") or {}).get("score") or 0),
            "confidence": (
                ((row.get("opportunity") or {}).get("components") or {}).get("confidence")
                or 0
            ),
        }
        for index, row in enumerate(top, start=1)
    ]

    return _public({
        "status": "ready" if top else "no_opportunities",
        "scope": scope.strip() or "AI",
        "database": database,
        "config": str(config_target),
        "init": init_result,
        "providers": provider_status,
        "top_opportunities": summary,
        "read_model": radar_result.get("read_model"),
        "dashboard": {
            "command": f"python bootstrap.py serve --db {database}",
            "url": "http://127.0.0.1:8787/",
        },
    })


def _print(payload: Any) -> None:
    print(
        json.dumps(
            _public(payload),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Content Opportunity Radar local bootstrap"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="Check local Radar readiness")
    doctor_parser.add_argument("--config", default=DEFAULT_CONFIG)
    doctor_parser.add_argument("--db", default=DEFAULT_DB)

    prepare_parser = sub.add_parser(
        "prepare",
        help="Initialize/migrate local Radar schemas without network access",
    )
    prepare_parser.add_argument("--db", default=DEFAULT_DB)

    init_parser = sub.add_parser("init", help="Create safe public-source local config")
    init_parser.add_argument("--scope", default="AI")
    init_parser.add_argument("--config", default=DEFAULT_CONFIG)
    init_parser.add_argument("--db", default=DEFAULT_DB)
    init_parser.add_argument("--force", action="store_true")

    collect_parser = sub.add_parser("collect", help="Run due collector jobs")
    collect_parser.add_argument("--config", default=DEFAULT_CONFIG)
    collect_parser.add_argument("--force", action="store_true")

    radar_parser = sub.add_parser("radar", help="Run discovery and persist read model")
    radar_parser.add_argument("--scope", default="AI")
    radar_parser.add_argument("--db", default=DEFAULT_DB)
    radar_parser.add_argument("--top", type=int, default=5)
    radar_parser.add_argument("--seed-limit", type=int, default=30)
    radar_parser.add_argument("--candidates", type=int, default=8)
    radar_parser.add_argument("--deep-limit", type=int, default=10)

    demo_parser = sub.add_parser("demo", help="Run collect + Radar product loop")
    demo_parser.add_argument("--scope", default="AI")
    demo_parser.add_argument("--config", default=DEFAULT_CONFIG)
    demo_parser.add_argument("--db", default=DEFAULT_DB)
    demo_parser.add_argument("--top", type=int, default=5)
    demo_parser.add_argument("--force-config", action="store_true")

    serve_parser = sub.add_parser("serve", help="Serve read-only API + Dashboard")
    serve_parser.add_argument("--db", default=DEFAULT_DB)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)

    args = parser.parse_args()

    if args.command == "doctor":
        result = doctor(
            config_path=args.config,
            database=args.db,
        )
        _print(result)
        return 0 if result["ready"] else 1

    if args.command == "prepare":
        _print(prepare(database=args.db))
        return 0

    if args.command == "init":
        _print(
            write_local_config(
                path=args.config,
                scope=args.scope,
                database=args.db,
                overwrite=args.force,
            )
        )
        return 0

    if args.command == "collect":
        _print(
            collect(
                config_path=args.config,
                force=args.force,
            )
        )
        return 0

    if args.command == "radar":
        _print(
            radar(
                scope=args.scope,
                database=args.db,
                top_n=args.top,
                seed_limit=args.seed_limit,
                candidate_count=args.candidates,
                deep_limit=args.deep_limit,
            )
        )
        return 0

    if args.command == "demo":
        _print(
            demo(
                scope=args.scope,
                config_path=args.config,
                database=args.db,
                overwrite_config=args.force_config,
                top_n=args.top,
            )
        )
        return 0

    if args.command == "serve":
        # Safe for an empty persistent volume: schema preparation is local-only
        # and idempotent, then the HTTP process remains strictly read-only.
        prepare(database=args.db)
        serve_api(
            database=args.db,
            host=args.host,
            port=max(1, min(args.port, 65535)),
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
