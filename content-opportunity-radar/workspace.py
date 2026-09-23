"""Workspace / Onboarding V1 for Content Opportunity Radar.

A workspace is the durable project boundary for one domain/customer context.
Secrets are never stored in the workspace profile; credential-bearing providers
use environment-variable references in generated collection plans.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from collector import CollectionService, CollectorConfig
from core import isoformat, stable_id, utcnow
from radar import run_discovery
from read_model import safe_warning


DEFAULT_DB = ".radar/radar.db"
MAX_COMPETITORS = 20


def normalize_site_url(value: str | None, *, required: bool = False) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise ValueError("site URL is required")
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("site URL must be absolute http/https")
    host = parsed.hostname.casefold()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme.casefold()}://{host}"


def normalize_gsc_site(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("sc-domain:"):
        domain = raw[len("sc-domain:"):].strip().casefold()
        if not domain or "/" in domain or " " in domain:
            raise ValueError("invalid sc-domain Search Console property")
        return f"sc-domain:{domain}"
    return normalize_site_url(raw, required=True)


def _clean_customer_id(value: str | None) -> str | None:
    raw = re.sub(r"[^0-9]", "", str(value or ""))
    return raw or None


def _clean_text(value: str | None, *, field: str, required: bool = False, max_len: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_len:
        raise ValueError(f"{field} exceeds {max_len} characters")
    return text


def _competitors(values: Sequence[str] | None) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        url = normalize_site_url(raw, required=True)
        if url in seen:
            continue
        seen.add(url)
        rows.append(url)
    if len(rows) > MAX_COMPETITORS:
        raise ValueError(f"competitors cannot exceed {MAX_COMPETITORS}")
    return rows


@dataclass(slots=True)
class Workspace:
    workspace_id: str
    name: str
    domain: str | None
    scope: str
    language: str
    country: str
    audience: str
    objective: str
    competitors: list[str]
    gsc_site_url: str | None
    google_ads_customer_id: str | None
    enabled: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceStore:
    def __init__(
        self,
        path: str | Path = DEFAULT_DB,
        *,
        read_only: bool = False,
        initialize: bool = True,
    ) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            uri = f"file:{self.path.resolve()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True)
            self.conn.execute("PRAGMA query_only=ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.row_factory = sqlite3.Row
        if initialize:
            if read_only:
                raise ValueError("read-only WorkspaceStore cannot initialize schema")
            self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "WorkspaceStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                domain TEXT,
                scope TEXT NOT NULL,
                language TEXT NOT NULL,
                country TEXT NOT NULL,
                audience TEXT NOT NULL,
                objective TEXT NOT NULL,
                competitors_json TEXT NOT NULL,
                gsc_site_url TEXT,
                google_ads_customer_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workspaces_enabled_name
            ON workspaces(enabled DESC, name ASC, workspace_id ASC);
            """
        )
        self.conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> Workspace:
        try:
            competitors = json.loads(row["competitors_json"] or "[]")
        except Exception:
            competitors = []
        return Workspace(
            workspace_id=row["workspace_id"],
            name=row["name"],
            domain=row["domain"],
            scope=row["scope"],
            language=row["language"],
            country=row["country"],
            audience=row["audience"],
            objective=row["objective"],
            competitors=list(competitors),
            gsc_site_url=row["gsc_site_url"],
            google_ads_customer_id=row["google_ads_customer_id"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create(
        self,
        *,
        name: str,
        scope: str,
        domain: str | None = None,
        language: str = "en",
        country: str = "US",
        audience: str = "",
        objective: str = "",
        competitors: Sequence[str] | None = None,
        gsc_site_url: str | None = None,
        google_ads_customer_id: str | None = None,
    ) -> Workspace:
        clean_name = _clean_text(name, field="name", required=True, max_len=120)
        clean_scope = _clean_text(scope, field="scope", required=True, max_len=160)
        clean_domain = normalize_site_url(domain) if domain else None
        clean_language = _clean_text(language, field="language", required=True, max_len=20)
        clean_country = _clean_text(country, field="country", required=True, max_len=20).upper()
        clean_audience = _clean_text(audience, field="audience", max_len=500)
        clean_objective = _clean_text(objective, field="objective", max_len=500)
        clean_competitors = _competitors(competitors)
        clean_gsc = normalize_gsc_site(gsc_site_url)
        clean_ads = _clean_customer_id(google_ads_customer_id)

        identity = clean_domain or clean_name.casefold()
        workspace_id = "ws_" + stable_id("workspace", identity, clean_scope)
        now = isoformat(utcnow())

        try:
            self.conn.execute(
                """
                INSERT INTO workspaces (
                    workspace_id, name, domain, scope, language, country,
                    audience, objective, competitors_json, gsc_site_url,
                    google_ads_customer_id, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    workspace_id,
                    clean_name,
                    clean_domain,
                    clean_scope,
                    clean_language,
                    clean_country,
                    clean_audience,
                    clean_objective,
                    json.dumps(clean_competitors, ensure_ascii=False),
                    clean_gsc,
                    clean_ads,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"workspace already exists: {workspace_id}") from exc
        self.conn.commit()
        return self.get(workspace_id)

    def get(self, workspace_id: str) -> Workspace:
        row = self.conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if not row:
            raise KeyError(workspace_id)
        return self._row(row)

    def list(self, *, include_disabled: bool = True) -> list[Workspace]:
        where = "" if include_disabled else "WHERE enabled = 1"
        rows = self.conn.execute(
            f"""
            SELECT * FROM workspaces
            {where}
            ORDER BY enabled DESC, name ASC, workspace_id ASC
            """
        ).fetchall()
        return [self._row(row) for row in rows]

    def set_enabled(self, workspace_id: str, enabled: bool) -> Workspace:
        now = isoformat(utcnow())
        cursor = self.conn.execute(
            """
            UPDATE workspaces
            SET enabled = ?, updated_at = ?
            WHERE workspace_id = ?
            """,
            (1 if enabled else 0, now, workspace_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(workspace_id)
        self.conn.commit()
        return self.get(workspace_id)

    def update(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        scope: str | None = None,
        domain: str | None = None,
        language: str | None = None,
        country: str | None = None,
        audience: str | None = None,
        objective: str | None = None,
        competitors: Sequence[str] | None = None,
        gsc_site_url: str | None = None,
        google_ads_customer_id: str | None = None,
    ) -> Workspace:
        current = self.get(workspace_id)
        values = {
            "name": current.name if name is None else _clean_text(name, field="name", required=True, max_len=120),
            "scope": current.scope if scope is None else _clean_text(scope, field="scope", required=True, max_len=160),
            "domain": current.domain if domain is None else normalize_site_url(domain),
            "language": current.language if language is None else _clean_text(language, field="language", required=True, max_len=20),
            "country": current.country if country is None else _clean_text(country, field="country", required=True, max_len=20).upper(),
            "audience": current.audience if audience is None else _clean_text(audience, field="audience", max_len=500),
            "objective": current.objective if objective is None else _clean_text(objective, field="objective", max_len=500),
            "competitors": current.competitors if competitors is None else _competitors(competitors),
            "gsc_site_url": current.gsc_site_url if gsc_site_url is None else normalize_gsc_site(gsc_site_url),
            "google_ads_customer_id": current.google_ads_customer_id if google_ads_customer_id is None else _clean_customer_id(google_ads_customer_id),
        }
        now = isoformat(utcnow())
        self.conn.execute(
            """
            UPDATE workspaces
            SET name=?, domain=?, scope=?, language=?, country=?,
                audience=?, objective=?, competitors_json=?, gsc_site_url=?,
                google_ads_customer_id=?, updated_at=?
            WHERE workspace_id=?
            """,
            (
                values["name"],
                values["domain"],
                values["scope"],
                values["language"],
                values["country"],
                values["audience"],
                values["objective"],
                json.dumps(values["competitors"], ensure_ascii=False),
                values["gsc_site_url"],
                values["google_ads_customer_id"],
                now,
                workspace_id,
            ),
        )
        self.conn.commit()
        return self.get(workspace_id)


def workspace_collection_plan(
    workspace: Workspace,
    *,
    database: str = DEFAULT_DB,
) -> dict[str, Any]:
    prefix = workspace.workspace_id
    jobs: list[dict[str, Any]] = [
        {
            "id": f"{prefix}-github",
            "provider": "github",
            "topic": workspace.scope,
            "interval_seconds": 1800,
            "limit": 30,
            "language": workspace.language,
            "country": workspace.country,
        },
        {
            "id": f"{prefix}-hackernews",
            "provider": "hackernews",
            "topic": workspace.scope,
            "interval_seconds": 900,
            "limit": 30,
            "language": workspace.language,
            "country": workspace.country,
        },
        {
            "id": f"{prefix}-google-news",
            "provider": "google_news",
            "topic": workspace.scope,
            "interval_seconds": 1800,
            "limit": 30,
            "language": workspace.language,
            "country": workspace.country,
        },
        {
            "id": f"{prefix}-gdelt",
            "provider": "gdelt",
            "topic": workspace.scope,
            "interval_seconds": 1800,
            "limit": 30,
            "language": workspace.language,
            "country": workspace.country,
        },
    ]

    if workspace.domain:
        jobs.append({
            "id": f"{prefix}-website-own",
            "provider": "website",
            "topic": workspace.scope,
            "interval_seconds": 21600,
            "limit": 100,
            "language": workspace.language,
            "country": workspace.country,
            "metadata": {
                "url": workspace.domain,
                "purpose": "own_site",
            },
        })

    for index, competitor in enumerate(workspace.competitors, start=1):
        jobs.append({
            "id": f"{prefix}-competitor-{index:02d}",
            "provider": "website",
            "topic": workspace.scope,
            "interval_seconds": 21600,
            "limit": 100,
            "language": workspace.language,
            "country": workspace.country,
            "metadata": {
                "url": competitor,
                "purpose": "competitor",
            },
        })

    if workspace.gsc_site_url:
        jobs.append({
            "id": f"{prefix}-gsc",
            "provider": "gsc",
            "topic": workspace.scope,
            "interval_seconds": 21600,
            "limit": 5000,
            "language": workspace.language,
            "country": workspace.country,
            "metadata": {
                "site_url": workspace.gsc_site_url,
                "query_filter": "",
                "dimensions": ["date", "query", "page"],
                "row_limit": 5000,
                "max_rows": 25000,
            },
            "metadata_env": {
                "access_token": "GSC_ACCESS_TOKEN",
            },
        })

    if workspace.google_ads_customer_id:
        jobs.append({
            "id": f"{prefix}-keyword-planner",
            "provider": "keyword_planner",
            "topic": workspace.scope,
            "interval_seconds": 604800,
            "limit": 20,
            "language": workspace.language,
            "country": workspace.country,
            "metadata": {
                "customer_id": workspace.google_ads_customer_id,
                "keywords": [workspace.scope],
            },
            "metadata_env": {
                "access_token": "GOOGLE_ADS_ACCESS_TOKEN",
            },
        })

    plan = {"database": database, "jobs": jobs}
    # Validate against the collector contract before returning/persisting.
    CollectorConfig.from_dict(plan)
    return plan


def run_workspace(
    workspace_id: str,
    *,
    database: str = DEFAULT_DB,
    force_collect: bool = True,
    top_n: int = 5,
) -> dict[str, Any]:
    with WorkspaceStore(database) as store:
        workspace = store.get(workspace_id)
    if not workspace.enabled:
        raise ValueError("workspace is disabled")

    plan = workspace_collection_plan(workspace, database=database)
    config = CollectorConfig.from_dict(plan)
    collection = CollectionService(config).run_once(force=force_collect)

    base = Path(database).parent
    radar_report = run_discovery(
        scope=workspace.scope,
        language=workspace.language,
        country=workspace.country,
        top_n=max(1, min(top_n, 20)),
        snapshot_path=str(base / "snapshots.jsonl"),
        cache_path=str(base / "provider-cache.json"),
        website=workspace.domain,
        gsc_site_url=workspace.gsc_site_url,
        gsc_access_token=os.getenv("GSC_ACCESS_TOKEN"),
        google_ads_customer_id=workspace.google_ads_customer_id,
        google_ads_access_token=os.getenv("GOOGLE_ADS_ACCESS_TOKEN"),
        hydrate_content=bool(workspace.domain and workspace.gsc_site_url),
        content_snapshot_path=str(base / "page-snapshots.jsonl"),
        collector_db=database,
        collector_since_hours=72,
        read_model_db=database,
        workspace_id=workspace.workspace_id,
        include_research_pack=True,
    )

    return {
        "workspace": workspace.to_dict(),
        "collection": {
            "jobs": [
                {
                    "job_id": row.get("job_id"),
                    "provider": row.get("provider"),
                    "status": row.get("status") or row.get("reason"),
                    "event_count": int(row.get("event_count") or 0),
                    "warnings": [safe_warning(item) for item in (row.get("warnings") or [])],
                }
                for row in collection.get("jobs") or []
            ],
        },
        "read_model": radar_report.get("read_model"),
        "top_opportunities": [
            {
                "rank": rank,
                "topic": row.get("topic"),
                "rank_score": row.get("rank_score"),
                "opportunity_score": (row.get("opportunity") or {}).get("score"),
            }
            for rank, row in enumerate(radar_report.get("top_opportunities") or [], start=1)
        ],
    }


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Content Opportunity Radar workspace onboarding")
    parser.add_argument("--db", default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--scope", required=True)
    create.add_argument("--domain")
    create.add_argument("--language", default="en")
    create.add_argument("--country", default="US")
    create.add_argument("--audience", default="")
    create.add_argument("--objective", default="")
    create.add_argument("--competitor", action="append", default=[])
    create.add_argument("--gsc-site")
    create.add_argument("--google-ads-customer")

    sub.add_parser("list")

    show = sub.add_parser("show")
    show.add_argument("workspace_id")

    run = sub.add_parser("run")
    run.add_argument("workspace_id")
    run.add_argument("--top", type=int, default=5)
    run.add_argument("--no-force-collect", action="store_true")

    enable = sub.add_parser("enable")
    enable.add_argument("workspace_id")

    disable = sub.add_parser("disable")
    disable.add_argument("workspace_id")

    plan = sub.add_parser("plan")
    plan.add_argument("workspace_id")

    args = parser.parse_args()

    if args.command == "create":
        with WorkspaceStore(args.db) as store:
            workspace = store.create(
                name=args.name,
                scope=args.scope,
                domain=args.domain,
                language=args.language,
                country=args.country,
                audience=args.audience,
                objective=args.objective,
                competitors=args.competitor,
                gsc_site_url=args.gsc_site,
                google_ads_customer_id=args.google_ads_customer,
            )
        _print(workspace.to_dict())
        return 0

    if args.command == "list":
        with WorkspaceStore(args.db) as store:
            rows = [item.to_dict() for item in store.list()]
        _print({"items": rows})
        return 0

    if args.command == "show":
        with WorkspaceStore(args.db) as store:
            workspace = store.get(args.workspace_id)
        _print(workspace.to_dict())
        return 0

    if args.command in {"enable", "disable"}:
        with WorkspaceStore(args.db) as store:
            workspace = store.set_enabled(
                args.workspace_id,
                args.command == "enable",
            )
        _print(workspace.to_dict())
        return 0

    if args.command == "plan":
        with WorkspaceStore(args.db) as store:
            workspace = store.get(args.workspace_id)
        _print(workspace_collection_plan(workspace, database=args.db))
        return 0

    if args.command == "run":
        _print(
            run_workspace(
                args.workspace_id,
                database=args.db,
                force_collect=not args.no_force_collect,
                top_n=args.top,
            )
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
