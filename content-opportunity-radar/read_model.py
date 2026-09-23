"""Stable product-facing read model for Content Opportunity Radar.

This module is deliberately read/persistence oriented:
- it never calls external providers;
- it stores append-only Radar run/opportunity snapshots;
- it stores only evidence actually referenced by ranked opportunities;
- it exposes safe, transport-independent query methods for the HTTP API.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core import isoformat, stable_id, utcnow


SENSITIVE_MARKERS = (
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


def _sensitive_key(value: object) -> bool:
    normalized = str(value or "").casefold().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_MARKERS)


def _sanitize_public(value: Any) -> Any:
    """Recursively remove credential-shaped fields from product-facing data."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _sensitive_key(key)
                else _sanitize_public(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_public(item) for item in value]
    return value


_WARNING_SECRET_RE = re.compile(
    r"(?i)\b(authorization|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"api[_-]?key|cookie|secret|password|private[_-]?key|credential)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def safe_warning(value: object) -> str:
    text = str(value or "")
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return _WARNING_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )


def _json(value: Any) -> str:
    return json.dumps(
        _sanitize_public(value),
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _encode_cursor(run_id: str, rank: int, topic_id: str) -> str:
    payload = json.dumps(
        {"run_id": run_id, "rank": rank, "topic_id": topic_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        data = json.loads(raw)
        return {
            "run_id": str(data["run_id"]),
            "rank": int(data["rank"]),
            "topic_id": str(data["topic_id"]),
        }
    except Exception as exc:
        raise ValueError("invalid cursor") from exc


@dataclass(slots=True)
class RadarRunRecord:
    run_id: str
    scope: str
    generated_at: str
    seed_source_mode: str
    candidate_count: int
    scanned_count: int
    warnings: list[str]
    scan_errors: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scope": self.scope,
            "generated_at": self.generated_at,
            "seed_source_mode": self.seed_source_mode,
            "candidate_count": self.candidate_count,
            "scanned_count": self.scanned_count,
            "warnings": self.warnings,
            "scan_errors": self.scan_errors,
        }


class OpportunityReadStore:
    """Append-only Radar product read model backed by SQLite."""

    def __init__(
        self,
        path: str | Path,
        *,
        initialize: bool = True,
        read_only: bool = False,
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
            self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        if initialize:
            if read_only:
                raise ValueError("read-only store cannot initialize schema")
            self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "OpportunityReadStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS radar_runs (
                run_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                seed_source_mode TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                scanned_count INTEGER NOT NULL,
                warning_json TEXT NOT NULL,
                error_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_radar_runs_scope_time
            ON radar_runs(scope, generated_at DESC, run_id DESC);

            CREATE TABLE IF NOT EXISTS opportunity_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                rank INTEGER NOT NULL,
                discovery_score REAL NOT NULL,
                opportunity_score REAL NOT NULL,
                rank_score REAL NOT NULL,
                confidence REAL NOT NULL,
                freshness REAL NOT NULL,
                components_json TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                features_json TEXT NOT NULL,
                discovery_json TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,
                research_pack_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES radar_runs(run_id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunity_run_topic
            ON opportunity_snapshots(run_id, topic_id);

            CREATE INDEX IF NOT EXISTS idx_opportunity_scope_rank
            ON opportunity_snapshots(scope, run_id, rank, topic_id);

            CREATE INDEX IF NOT EXISTS idx_opportunity_topic_time
            ON opportunity_snapshots(topic_id, created_at DESC, snapshot_id DESC);

            CREATE TABLE IF NOT EXISTS radar_evidence (
                evidence_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                source TEXT NOT NULL,
                acquisition_method TEXT,
                url TEXT,
                title TEXT,
                text TEXT,
                author TEXT,
                community TEXT,
                published_at TEXT,
                retrieved_at TEXT,
                language TEXT,
                country TEXT,
                metrics_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_radar_evidence_provider_time
            ON radar_evidence(provider, last_seen_at DESC, evidence_id);
            """
        )
        self.conn.commit()

    def _table_exists(self, name: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name=?
            """,
            (name,),
        ).fetchone()
        return bool(row)

    def _upsert_evidence(self, event: Mapping[str, Any], now: str) -> None:
        evidence_id = str(event.get("id") or "").strip()
        provider = str(event.get("provider") or "").strip()
        source = str(event.get("source") or "").strip()
        if not evidence_id or not provider or not source:
            return

        provenance = _sanitize_public(event.get("provenance") or {})
        metrics = _sanitize_public(event.get("metrics") or {})
        self.conn.execute(
            """
            INSERT INTO radar_evidence (
                evidence_id, provider, source, acquisition_method, url,
                title, text, author, community, published_at, retrieved_at,
                language, country, metrics_json, provenance_json,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                provider=excluded.provider,
                source=excluded.source,
                acquisition_method=COALESCE(excluded.acquisition_method, radar_evidence.acquisition_method),
                url=COALESCE(excluded.url, radar_evidence.url),
                title=COALESCE(excluded.title, radar_evidence.title),
                text=COALESCE(excluded.text, radar_evidence.text),
                author=COALESCE(excluded.author, radar_evidence.author),
                community=COALESCE(excluded.community, radar_evidence.community),
                published_at=COALESCE(excluded.published_at, radar_evidence.published_at),
                retrieved_at=COALESCE(excluded.retrieved_at, radar_evidence.retrieved_at),
                language=COALESCE(excluded.language, radar_evidence.language),
                country=COALESCE(excluded.country, radar_evidence.country),
                metrics_json=excluded.metrics_json,
                provenance_json=excluded.provenance_json,
                last_seen_at=excluded.last_seen_at
            """,
            (
                evidence_id,
                provider,
                source,
                event.get("acquisition_method"),
                event.get("url"),
                event.get("title"),
                event.get("text"),
                event.get("author"),
                event.get("community"),
                event.get("published_at"),
                event.get("retrieved_at"),
                event.get("language"),
                event.get("country"),
                _json(metrics),
                _json(provenance),
                now,
                now,
            ),
        )

    def record_discovery(
        self,
        report: Mapping[str, Any],
        *,
        generated_at: datetime | None = None,
    ) -> str:
        """Persist one Radar run and its ranked Top Opportunities append-only."""
        now_dt = generated_at or utcnow()
        generated = isoformat(now_dt)
        scope = str(report.get("scope") or "default")
        seed = dict(report.get("seed") or {})
        warnings = [safe_warning(item) for item in (seed.get("warnings") or [])]
        scan_errors = [
            _sanitize_public(item)
            for item in (report.get("scan_errors") or [])
            if isinstance(item, Mapping)
        ]
        run_id = stable_id(
            "radar-run",
            scope,
            generated,
            report.get("candidate_count"),
            report.get("scanned_count"),
        )

        self.conn.execute(
            """
            INSERT INTO radar_runs (
                run_id, scope, generated_at, seed_source_mode,
                candidate_count, scanned_count, warning_json, error_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                scope,
                generated,
                str(seed.get("source_mode") or "unknown"),
                int(report.get("candidate_count") or 0),
                int(report.get("scanned_count") or 0),
                _json(warnings),
                _json(scan_errors),
            ),
        )

        for rank, row in enumerate(report.get("top_opportunities") or [], start=1):
            opportunity = dict(row.get("opportunity") or {})
            discovery = dict(row.get("discovery") or {})
            components = dict(opportunity.get("components") or {})
            evidence_ids = sorted(
                {
                    str(item)
                    for item in (opportunity.get("evidence_ids") or [])
                    if item
                }
            )
            snapshot_id = stable_id("opportunity", run_id, row.get("topic_id"), rank)
            self.conn.execute(
                """
                INSERT INTO opportunity_snapshots (
                    snapshot_id, run_id, scope, topic_id, topic_name, rank,
                    discovery_score, opportunity_score, rank_score,
                    confidence, freshness, components_json, reasons_json,
                    features_json, discovery_json, evidence_ids_json,
                    research_pack_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_id,
                    scope,
                    str(row.get("topic_id") or ""),
                    str(row.get("topic") or ""),
                    rank,
                    float(discovery.get("discovery_score") or 0.0),
                    float(opportunity.get("score") or 0.0),
                    float(row.get("rank_score") or 0.0),
                    float(components.get("confidence") or 0.0),
                    float(components.get("freshness") or 0.0),
                    _json(components),
                    _json(opportunity.get("reasons") or []),
                    _json(row.get("features") or {}),
                    _json(discovery),
                    _json(evidence_ids),
                    _json(row.get("research_pack")) if row.get("research_pack") is not None else None,
                    generated,
                ),
            )

            wanted = set(evidence_ids)
            for event in row.get("events") or []:
                if str(event.get("id") or "") in wanted:
                    self._upsert_evidence(event, generated)

        self.conn.commit()
        return run_id

    def latest_run(self, scope: str) -> RadarRunRecord | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM radar_runs
            WHERE scope = ?
            ORDER BY generated_at DESC, run_id DESC
            LIMIT 1
            """,
            (scope,),
        ).fetchone()
        if not row:
            return None
        return RadarRunRecord(
            run_id=row["run_id"],
            scope=row["scope"],
            generated_at=row["generated_at"],
            seed_source_mode=row["seed_source_mode"],
            candidate_count=int(row["candidate_count"]),
            scanned_count=int(row["scanned_count"]),
            warnings=[safe_warning(item) for item in _loads(row["warning_json"], [])],
            scan_errors=list(_loads(row["error_json"], [])),
        )

    def _source_summary(self, evidence_ids: Sequence[str]) -> list[dict[str, Any]]:
        ids = [str(item) for item in evidence_ids if item]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"""
            SELECT provider, source, COUNT(*) AS evidence_count,
                   MAX(last_seen_at) AS last_seen_at
            FROM radar_evidence
            WHERE evidence_id IN ({placeholders})
            GROUP BY provider, source
            ORDER BY evidence_count DESC, provider ASC, source ASC
            """,
            ids,
        ).fetchall()
        return [
            {
                "provider": row["provider"],
                "source": row["source"],
                "evidence_count": int(row["evidence_count"]),
                "last_seen_at": row["last_seen_at"],
            }
            for row in rows
        ]

    def _opportunity_row(self, row: sqlite3.Row) -> dict[str, Any]:
        evidence_ids = list(_loads(row["evidence_ids_json"], []))
        return {
            "run_id": row["run_id"],
            "scope": row["scope"],
            "topic_id": row["topic_id"],
            "topic": row["topic_name"],
            "rank": int(row["rank"]),
            "scores": {
                "discovery": float(row["discovery_score"]),
                "opportunity": float(row["opportunity_score"]),
                "rank": float(row["rank_score"]),
            },
            "components": dict(_loads(row["components_json"], {})),
            "confidence": float(row["confidence"]),
            "freshness": float(row["freshness"]),
            "reasons": list(_loads(row["reasons_json"], [])),
            "features": dict(_loads(row["features_json"], {})),
            "discovery": dict(_loads(row["discovery_json"], {})),
            "evidence_ids": evidence_ids,
            "source_summary": self._source_summary(evidence_ids),
            "research_pack_available": row["research_pack_json"] is not None,
            "created_at": row["created_at"],
        }

    def list_opportunities(
        self,
        *,
        scope: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        run = self.latest_run(scope)
        if not run:
            return {"run": None, "items": [], "next_cursor": None}

        page_size = max(1, min(int(limit), 100))
        decoded = _decode_cursor(cursor)
        params: list[Any] = [run.run_id]
        where = ["run_id = ?"]
        if decoded:
            if decoded["run_id"] != run.run_id:
                raise ValueError("cursor belongs to a different Radar run")
            where.append("(rank > ? OR (rank = ? AND topic_id > ?))")
            params.extend([
                decoded["rank"],
                decoded["rank"],
                decoded["topic_id"],
            ])
        params.append(page_size + 1)
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM opportunity_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY rank ASC, topic_id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        page = rows[:page_size]
        next_cursor = None
        if len(rows) > page_size and page:
            last = page[-1]
            next_cursor = _encode_cursor(
                run.run_id,
                int(last["rank"]),
                str(last["topic_id"]),
            )
        return {
            "run": run.to_dict(),
            "items": [self._opportunity_row(row) for row in page],
            "next_cursor": next_cursor,
        }

    def get_opportunity(
        self,
        topic_id: str,
        *,
        scope: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [topic_id]
        where = ["topic_id = ?"]
        if scope is not None:
            where.append("scope = ?")
            params.append(scope)
        row = self.conn.execute(
            f"""
            SELECT *
            FROM opportunity_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return self._opportunity_row(row) if row else None

    def opportunity_history(
        self,
        topic_id: str,
        *,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [topic_id]
        where = ["topic_id = ?"]
        if scope is not None:
            where.append("scope = ?")
            params.append(scope)
        params.append(max(1, min(int(limit), 365)))
        rows = self.conn.execute(
            f"""
            SELECT run_id, scope, topic_id, topic_name, rank,
                   discovery_score, opportunity_score, rank_score,
                   confidence, freshness, created_at
            FROM opportunity_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "scope": row["scope"],
                "topic_id": row["topic_id"],
                "topic": row["topic_name"],
                "rank": int(row["rank"]),
                "scores": {
                    "discovery": float(row["discovery_score"]),
                    "opportunity": float(row["opportunity_score"]),
                    "rank": float(row["rank_score"]),
                },
                "confidence": float(row["confidence"]),
                "freshness": float(row["freshness"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM radar_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "evidence_id": row["evidence_id"],
            "provider": row["provider"],
            "source": row["source"],
            "acquisition_method": row["acquisition_method"],
            "url": row["url"],
            "title": row["title"],
            "text": row["text"],
            "author": row["author"],
            "community": row["community"],
            "published_at": row["published_at"],
            "retrieved_at": row["retrieved_at"],
            "language": row["language"],
            "country": row["country"],
            "metrics": dict(_loads(row["metrics_json"], {})),
            "provenance": dict(_loads(row["provenance_json"], {})),
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        }

    def research_pack(
        self,
        topic_id: str,
        *,
        scope: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [topic_id]
        where = ["topic_id = ?", "research_pack_json IS NOT NULL"]
        if scope is not None:
            where.append("scope = ?")
            params.append(scope)
        row = self.conn.execute(
            f"""
            SELECT research_pack_json
            FROM opportunity_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if not row:
            return None
        value = _loads(row["research_pack_json"], None)
        return _sanitize_public(value) if isinstance(value, Mapping) else value

    def provider_health(self) -> list[dict[str, Any]]:
        """Map collector state into a safe product-facing provider health view."""
        if not self._table_exists("job_state"):
            return []

        states = self.conn.execute(
            "SELECT * FROM job_state ORDER BY provider, job_id"
        ).fetchall()
        has_runs = self._table_exists("collection_runs")
        result: list[dict[str, Any]] = []
        for state in states:
            latest_run = None
            last_healthy = None
            if has_runs:
                latest_run = self.conn.execute(
                    """
                    SELECT finished_at, status, event_count, warning_json,
                           rate_remaining, rate_reset_at
                    FROM collection_runs
                    WHERE job_id = ?
                    ORDER BY finished_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    (state["job_id"],),
                ).fetchone()
                last_healthy = self.conn.execute(
                    """
                    SELECT MAX(finished_at) AS finished_at
                    FROM collection_runs
                    WHERE job_id = ? AND status = 'healthy'
                    """,
                    (state["job_id"],),
                ).fetchone()["finished_at"]

            status = str(state["last_status"] or "unknown")
            warnings = []
            if latest_run:
                warnings = [
                    safe_warning(item)
                    for item in _loads(latest_run["warning_json"], [])
                ]
            result.append(
                {
                    "job_id": state["job_id"],
                    "provider": state["provider"],
                    "status": status,
                    "last_attempted_at": state["last_finished_at"],
                    "last_successful_at": last_healthy,
                    "event_count": int(
                        latest_run["event_count"] if latest_run else state["last_event_count"] or 0
                    ),
                    "next_due_at": state["next_due_at"],
                    "rate_limit_reset_at": (
                        latest_run["rate_reset_at"]
                        if latest_run and latest_run["rate_reset_at"]
                        else state["rate_limit_reset_at"]
                    ),
                    "rate_remaining": latest_run["rate_remaining"] if latest_run else None,
                    "stale": status == "stale",
                    "degraded": status in {
                        "degraded",
                        "failed",
                        "rate_limited",
                        "auth_required",
                        "disabled",
                    },
                    "warnings": warnings,
                }
            )
        return result

    def health(self) -> dict[str, Any]:
        scopes = self.conn.execute(
            "SELECT COUNT(DISTINCT scope) FROM radar_runs"
        ).fetchone()[0]
        runs = self.conn.execute(
            "SELECT COUNT(*) FROM radar_runs"
        ).fetchone()[0]
        snapshots = self.conn.execute(
            "SELECT COUNT(*) FROM opportunity_snapshots"
        ).fetchone()[0]
        evidence = self.conn.execute(
            "SELECT COUNT(*) FROM radar_evidence"
        ).fetchone()[0]
        return {
            "status": "ok",
            "database": str(self.path),
            "radar_runs": int(runs),
            "opportunity_snapshots": int(snapshots),
            "evidence_records": int(evidence),
            "scopes": int(scopes),
            "provider_jobs": len(self.provider_health()),
        }
