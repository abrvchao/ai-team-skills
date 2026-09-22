"""Persistent acquisition service for Content Opportunity Radar.

This is the bridge from one-shot CLI collection to a durable signal dataset.

Examples:
    python collector.py init --config collection-plan.json
    python collector.py run-once --config collection-plan.json
    python collector.py status --config collection-plan.json
    python collector.py loop --config collection-plan.json --poll-seconds 30

Design rules:
- source credentials are injected from environment-variable references;
- request metadata / secrets are never persisted;
- RawEvent identity is deduplicated, while every observation remains append-only;
- metric snapshots are append-only;
- provider failures and rate limits are isolated per job;
- scheduling is source-centric and independent from LLM reasoning.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core import (
    CollectionRequest,
    CollectionResult,
    DataProvider,
    ProviderRegistry,
    ProviderState,
    RawEvent,
    isoformat,
    stable_id,
    utcnow,
)
from gsc import GSCProvider
from keyword_planner import KeywordPlannerProvider
from pipeline import (
    GDELTProvider,
    GitHubProvider,
    GoogleNewsProvider,
    HackerNewsProvider,
)
from web import WebsiteProvider


DEFAULT_DB = ".radar/radar.db"
SECRET_METADATA_KEYS = {
    "access_token",
    "token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "client_secret",
    "developer_token",
    "authorization",
    "cookie",
    "set_cookie",
    "set-cookie",
}


def _sensitive_key(value: object) -> bool:
    normalized = str(value or "").casefold().replace("-", "_")
    if normalized in SECRET_METADATA_KEYS:
        return True
    return any(
        marker in normalized
        for marker in (
            "access_token",
            "authorization",
            "client_secret",
            "api_key",
            "apikey",
            "password",
            "developer_token",
        )
    )


def _sanitize_for_storage(value: Any) -> Any:
    """Recursively redact credential-shaped fields before durable storage."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _sensitive_key(key)
                else _sanitize_for_storage(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_storage(item) for item in value]
    return value
SUCCESS_STATES = {
    ProviderState.HEALTHY,
    ProviderState.DEGRADED,
    ProviderState.STALE,
}
ProviderFactory = Callable[[], DataProvider]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def default_provider_factories() -> dict[str, ProviderFactory]:
    return {
        "github": GitHubProvider,
        "hackernews": HackerNewsProvider,
        "google_news": GoogleNewsProvider,
        "gdelt": GDELTProvider,
        "website": WebsiteProvider,
        "gsc": GSCProvider,
        "keyword_planner": KeywordPlannerProvider,
    }


@dataclass(slots=True)
class CollectionJob:
    id: str
    provider: str
    topic: str
    interval_seconds: int
    limit: int = 20
    language: str = "en"
    country: str = "US"
    metadata: dict[str, Any] = field(default_factory=dict)
    metadata_env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    max_backoff_seconds: int = 21600

    def validate(self) -> None:
        if not self.id.strip():
            raise ValueError("job id is required")
        if not self.provider.strip():
            raise ValueError(f"job {self.id}: provider is required")
        if not self.topic.strip():
            raise ValueError(f"job {self.id}: topic/scope is required")
        if self.interval_seconds < 60:
            raise ValueError(
                f"job {self.id}: interval_seconds must be >= 60"
            )
        if not 1 <= self.limit <= 5000:
            raise ValueError(f"job {self.id}: limit must be between 1 and 5000")

        secret_literals = {
            key for key in self.metadata
            if key.casefold() in SECRET_METADATA_KEYS
        }
        if secret_literals:
            names = ", ".join(sorted(secret_literals))
            raise ValueError(
                f"job {self.id}: secret metadata ({names}) must use metadata_env"
            )

        for key, env_name in self.metadata_env.items():
            if not key.strip() or not str(env_name).strip():
                raise ValueError(
                    f"job {self.id}: metadata_env requires non-empty key/env name"
                )

    def resolved_metadata(
        self,
        environ: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        environ = os.environ if environ is None else environ
        metadata = dict(self.metadata)
        missing: list[str] = []
        for key, env_name in self.metadata_env.items():
            value = environ.get(env_name)
            if value is None or value == "":
                missing.append(env_name)
                continue
            metadata[key] = value
        return metadata, missing


@dataclass(slots=True)
class CollectorConfig:
    database: str
    jobs: list[CollectionJob]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CollectorConfig":
        jobs: list[CollectionJob] = []
        seen: set[str] = set()
        for raw in data.get("jobs") or []:
            job = CollectionJob(
                id=str(raw.get("id") or "").strip(),
                provider=str(raw.get("provider") or "").strip(),
                topic=str(raw.get("topic") or "").strip(),
                interval_seconds=int(raw.get("interval_seconds") or 0),
                limit=int(raw.get("limit") or 20),
                language=str(raw.get("language") or "en"),
                country=str(raw.get("country") or "US"),
                metadata=dict(raw.get("metadata") or {}),
                metadata_env={
                    str(key): str(value)
                    for key, value in (raw.get("metadata_env") or {}).items()
                },
                enabled=bool(raw.get("enabled", True)),
                max_backoff_seconds=int(
                    raw.get("max_backoff_seconds") or 21600
                ),
            )
            job.validate()
            if job.id in seen:
                raise ValueError(f"duplicate job id: {job.id}")
            seen.add(job.id)
            jobs.append(job)

        if not jobs:
            raise ValueError("collector config requires at least one job")
        return cls(
            database=str(data.get("database") or DEFAULT_DB),
            jobs=jobs,
        )

    @classmethod
    def load(cls, path: str | Path) -> "CollectorConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


class RadarStore:
    """SQLite-backed raw event + observation + scheduler state store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "RadarStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_events (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                source TEXT NOT NULL,
                acquisition_method TEXT NOT NULL,
                external_id TEXT,
                url TEXT,
                title TEXT,
                text TEXT,
                author TEXT,
                community TEXT,
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                retrieval_count INTEGER NOT NULL DEFAULT 1,
                language TEXT,
                country TEXT,
                metrics_json TEXT NOT NULL,
                raw_json TEXT,
                provenance_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_observations (
                observation_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                raw_json TEXT,
                FOREIGN KEY(event_id) REFERENCES raw_events(id)
            );

            CREATE INDEX IF NOT EXISTS idx_event_observations_event_time
            ON event_observations(event_id, observed_at);

            CREATE INDEX IF NOT EXISTS idx_event_observations_job_time
            ON event_observations(job_id, observed_at);

            CREATE TABLE IF NOT EXISTS metric_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                collected_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                job_id TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_metric_series
            ON metric_snapshots(subject_type, subject_id, metric, provider, collected_at);

            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                event_count INTEGER NOT NULL,
                warning_json TEXT NOT NULL,
                rate_remaining INTEGER,
                rate_reset_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_collection_runs_job_time
            ON collection_runs(job_id, finished_at);

            CREATE TABLE IF NOT EXISTS job_state (
                job_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                last_started_at TEXT,
                last_finished_at TEXT,
                last_status TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                next_due_at TEXT,
                rate_limit_reset_at TEXT,
                last_event_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def state(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM job_state WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def all_states(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM job_state ORDER BY job_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def _upsert_event(
        self,
        event: RawEvent,
        *,
        observed_at: datetime,
        job_id: str,
    ) -> None:
        timestamp = isoformat(observed_at)
        payload = event.to_dict(include_raw=False)
        metrics_json = _json(event.metrics)
        raw_json = _json(_sanitize_for_storage(event.raw))
        provenance_json = _json(
            _sanitize_for_storage(payload.get("provenance") or {})
        )

        self.conn.execute(
            """
            INSERT INTO raw_events (
                id, provider, source, acquisition_method, external_id, url,
                title, text, author, community, published_at,
                first_seen_at, last_seen_at, retrieval_count,
                language, country, metrics_json, raw_json, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source=excluded.source,
                external_id=COALESCE(excluded.external_id, raw_events.external_id),
                url=COALESCE(excluded.url, raw_events.url),
                title=COALESCE(excluded.title, raw_events.title),
                text=COALESCE(excluded.text, raw_events.text),
                author=COALESCE(excluded.author, raw_events.author),
                community=COALESCE(excluded.community, raw_events.community),
                published_at=COALESCE(excluded.published_at, raw_events.published_at),
                last_seen_at=excluded.last_seen_at,
                retrieval_count=raw_events.retrieval_count + 1,
                language=COALESCE(excluded.language, raw_events.language),
                country=COALESCE(excluded.country, raw_events.country),
                metrics_json=excluded.metrics_json,
                raw_json=excluded.raw_json,
                provenance_json=excluded.provenance_json
            """,
            (
                event.id,
                event.provider,
                event.source,
                event.acquisition_method.value,
                event.external_id,
                event.url,
                event.title,
                event.text,
                event.author,
                event.community,
                isoformat(event.published_at),
                timestamp,
                timestamp,
                event.language,
                event.country,
                metrics_json,
                raw_json,
                provenance_json,
            ),
        )

        observation_id = stable_id(
            "observation",
            job_id,
            event.id,
            timestamp,
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_observations (
                observation_id, event_id, job_id, provider,
                observed_at, metrics_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                event.id,
                job_id,
                event.provider,
                timestamp,
                metrics_json,
                raw_json,
            ),
        )

        for metric, value in sorted(event.metrics.items()):
            snapshot_id = stable_id(
                "metric",
                job_id,
                event.id,
                metric,
                timestamp,
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO metric_snapshots (
                    snapshot_id, subject_type, subject_id, metric, value,
                    collected_at, provider, job_id
                ) VALUES (?, 'event', ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    event.id,
                    str(metric),
                    float(value),
                    timestamp,
                    event.provider,
                    job_id,
                ),
            )

    def record_result(
        self,
        job: CollectionJob,
        result: CollectionResult,
        *,
        started_at: datetime,
        finished_at: datetime,
        next_due_at: datetime,
        consecutive_failures: int,
    ) -> None:
        run_id = stable_id(
            "run",
            job.id,
            isoformat(started_at),
            isoformat(finished_at),
        )
        observed_at = result.collected_at or finished_at

        for event in result.events:
            self._upsert_event(
                event,
                observed_at=observed_at,
                job_id=job.id,
            )

        timestamp = isoformat(observed_at)
        topic_subject = f"collection:{job.id}"
        aggregate_metrics = {
            "event_count": float(len(result.events)),
            "provider_healthy": 1.0 if result.status == ProviderState.HEALTHY else 0.0,
        }
        for metric, value in aggregate_metrics.items():
            snapshot_id = stable_id(
                "collection-metric",
                job.id,
                metric,
                timestamp,
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO metric_snapshots (
                    snapshot_id, subject_type, subject_id, metric, value,
                    collected_at, provider, job_id
                ) VALUES (?, 'collection_job', ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    topic_subject,
                    metric,
                    value,
                    timestamp,
                    result.provider,
                    job.id,
                ),
            )

        rate_remaining = (
            result.rate_limit.remaining
            if result.rate_limit else None
        )
        rate_reset = (
            isoformat(result.rate_limit.reset_at)
            if result.rate_limit else None
        )
        self.conn.execute(
            """
            INSERT INTO collection_runs (
                run_id, job_id, provider, started_at, finished_at, status,
                event_count, warning_json, rate_remaining, rate_reset_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                job.id,
                result.provider,
                isoformat(started_at),
                isoformat(finished_at),
                result.status.value,
                len(result.events),
                _json(result.warnings),
                rate_remaining,
                rate_reset,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO job_state (
                job_id, provider, last_started_at, last_finished_at,
                last_status, consecutive_failures, next_due_at,
                rate_limit_reset_at, last_event_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                provider=excluded.provider,
                last_started_at=excluded.last_started_at,
                last_finished_at=excluded.last_finished_at,
                last_status=excluded.last_status,
                consecutive_failures=excluded.consecutive_failures,
                next_due_at=excluded.next_due_at,
                rate_limit_reset_at=excluded.rate_limit_reset_at,
                last_event_count=excluded.last_event_count,
                updated_at=excluded.updated_at
            """,
            (
                job.id,
                result.provider,
                isoformat(started_at),
                isoformat(finished_at),
                result.status.value,
                consecutive_failures,
                isoformat(next_due_at),
                rate_reset,
                len(result.events),
                isoformat(finished_at),
            ),
        )
        self.conn.commit()

    def recent_events(
        self,
        *,
        since: datetime,
        providers: Sequence[str] | None = None,
        limit: int = 5000,
    ) -> list[RawEvent]:
        """Return latest deduped events observed since a cutoff.

        This is intentionally a latest-state event view. Historical metric
        movement remains in event_observations / metric_snapshots.
        """
        params: list[Any] = [isoformat(since)]
        where = ["last_seen_at >= ?"]
        if providers:
            placeholders = ",".join("?" for _ in providers)
            where.append(f"provider IN ({placeholders})")
            params.extend(providers)
        params.append(max(1, min(int(limit), 50000)))

        rows = self.conn.execute(
            f"""
            SELECT * FROM raw_events
            WHERE {' AND '.join(where)}
            ORDER BY last_seen_at DESC, id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        events: list[RawEvent] = []
        for row in rows:
            try:
                events.append(
                    RawEvent.from_dict({
                        "id": row["id"],
                        "provider": row["provider"],
                        "source": row["source"],
                        "acquisition_method": row["acquisition_method"],
                        "retrieved_at": row["last_seen_at"],
                        "external_id": row["external_id"],
                        "url": row["url"],
                        "title": row["title"],
                        "text": row["text"],
                        "author": row["author"],
                        "community": row["community"],
                        "published_at": row["published_at"],
                        "language": row["language"],
                        "country": row["country"],
                        "metrics": json.loads(row["metrics_json"] or "{}"),
                        "raw": json.loads(row["raw_json"] or "null"),
                        "provenance": json.loads(
                            row["provenance_json"] or "{}"
                        ),
                    })
                )
            except Exception:
                continue
        return events

    def counts(self) -> dict[str, int]:
        names = (
            "raw_events",
            "event_observations",
            "metric_snapshots",
            "collection_runs",
            "job_state",
        )
        return {
            name: int(
                self.conn.execute(
                    f"SELECT COUNT(*) FROM {name}"
                ).fetchone()[0]
            )
            for name in names
        }


class CollectionService:
    def __init__(
        self,
        config: CollectorConfig,
        *,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
        environ: Mapping[str, str] | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        self.config = config
        self.provider_factories = dict(
            provider_factories or default_provider_factories()
        )
        self.environ = os.environ if environ is None else environ
        self.now_fn = now_fn

    def _provider(self, provider_id: str) -> DataProvider:
        factory = self.provider_factories.get(provider_id)
        if not factory:
            raise ValueError(f"unsupported provider: {provider_id}")
        return factory()

    @staticmethod
    def _is_due(
        job: CollectionJob,
        state: Mapping[str, Any] | None,
        now: datetime,
    ) -> bool:
        if not job.enabled:
            return False
        if not state:
            return True

        rate_reset = _parse_time(state.get("rate_limit_reset_at"))
        if rate_reset and rate_reset > now:
            return False

        next_due = _parse_time(state.get("next_due_at"))
        return not next_due or next_due <= now

    @staticmethod
    def _next_schedule(
        job: CollectionJob,
        result: CollectionResult,
        now: datetime,
        prior_failures: int,
    ) -> tuple[datetime, int]:
        if result.status in SUCCESS_STATES:
            return now + timedelta(seconds=job.interval_seconds), 0

        if result.status == ProviderState.RATE_LIMITED:
            reset_at = (
                result.rate_limit.reset_at
                if result.rate_limit and result.rate_limit.reset_at
                else None
            )
            fallback = now + timedelta(
                seconds=max(300, min(job.interval_seconds, 3600))
            )
            return max(reset_at, fallback) if reset_at else fallback, prior_failures

        if result.status in {
            ProviderState.AUTH_REQUIRED,
            ProviderState.DISABLED,
        }:
            return now + timedelta(seconds=job.interval_seconds), prior_failures

        failures = prior_failures + 1
        backoff = min(
            job.max_backoff_seconds,
            job.interval_seconds * (2 ** min(failures, 8)),
        )
        return now + timedelta(seconds=backoff), failures

    def run_job(
        self,
        job: CollectionJob,
        store: RadarStore,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        now = self.now_fn()
        state = store.state(job.id)
        if not force and not self._is_due(job, state, now):
            return {
                "job_id": job.id,
                "provider": job.provider,
                "executed": False,
                "reason": "not_due",
                "next_due_at": state.get("next_due_at") if state else None,
            }

        started = now
        prior_failures = int((state or {}).get("consecutive_failures") or 0)
        metadata, missing_env = job.resolved_metadata(self.environ)

        try:
            provider = self._provider(job.provider)
            registry = ProviderRegistry()
            registry.register(provider)
            request = CollectionRequest(
                topic=job.topic,
                limit=job.limit,
                language=job.language,
                country=job.country,
                metadata=metadata,
            )
            result = registry.safe_collect(provider, request)
        except Exception as exc:
            result = CollectionResult(
                provider=job.provider,
                status=ProviderState.FAILED,
                warnings=[f"{type(exc).__name__}: {exc}"],
            )

        if missing_env:
            result.warnings.append(
                "missing environment variables: "
                + ", ".join(sorted(missing_env))
            )

        finished = self.now_fn()
        next_due, failures = self._next_schedule(
            job,
            result,
            finished,
            prior_failures,
        )
        store.record_result(
            job,
            result,
            started_at=started,
            finished_at=finished,
            next_due_at=next_due,
            consecutive_failures=failures,
        )
        return {
            "job_id": job.id,
            "provider": job.provider,
            "executed": True,
            "status": result.status.value,
            "event_count": len(result.events),
            "warnings": result.warnings,
            "next_due_at": isoformat(next_due),
            "rate_limit": {
                "remaining": result.rate_limit.remaining,
                "reset_at": isoformat(result.rate_limit.reset_at),
            } if result.rate_limit else None,
        }

    def run_once(
        self,
        *,
        force: bool = False,
        job_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        selected = set(job_ids or [])
        rows: list[dict[str, Any]] = []
        with RadarStore(self.config.database) as store:
            for job in self.config.jobs:
                if selected and job.id not in selected:
                    continue
                rows.append(self.run_job(job, store, force=force))
            counts = store.counts()
        return {
            "database": self.config.database,
            "jobs": rows,
            "counts": counts,
        }

    def status(self) -> dict[str, Any]:
        with RadarStore(self.config.database) as store:
            states = store.all_states()
            counts = store.counts()
        configured = {job.id: job for job in self.config.jobs}
        state_by_id = {row["job_id"]: row for row in states}
        jobs = []
        for job_id, job in configured.items():
            state = state_by_id.get(job_id)
            jobs.append({
                "job_id": job_id,
                "provider": job.provider,
                "enabled": job.enabled,
                "interval_seconds": job.interval_seconds,
                "state": state,
            })
        return {
            "database": self.config.database,
            "counts": counts,
            "jobs": jobs,
        }


EXAMPLE_CONFIG = {
    "database": ".radar/radar.db",
    "jobs": [
        {
            "id": "ai-github",
            "provider": "github",
            "topic": "AI",
            "interval_seconds": 1800,
            "limit": 30,
        },
        {
            "id": "ai-hackernews",
            "provider": "hackernews",
            "topic": "AI",
            "interval_seconds": 900,
            "limit": 30,
        },
        {
            "id": "ai-google-news",
            "provider": "google_news",
            "topic": "AI",
            "interval_seconds": 1800,
            "limit": 30,
        },
        {
            "id": "ai-gdelt",
            "provider": "gdelt",
            "topic": "AI",
            "interval_seconds": 1800,
            "limit": 30,
        },
        {
            "id": "my-site-gsc",
            "provider": "gsc",
            "topic": "site-wide",
            "interval_seconds": 21600,
            "limit": 5000,
            "metadata": {
                "site_url": "sc-domain:example.com",
                "query_filter": "",
                "dimensions": ["date", "query", "page"],
                "row_limit": 5000,
                "max_rows": 25000,
            },
            "metadata_env": {
                "access_token": "GSC_ACCESS_TOKEN",
            },
        },
        {
            "id": "agent-memory-keyword-baseline",
            "provider": "keyword_planner",
            "topic": "agent memory",
            "interval_seconds": 604800,
            "limit": 20,
            "metadata": {
                "customer_id": "1234567890",
                "keywords": ["agent memory", "ai agent memory"],
                "geo_target": "2840",
                "language_constant": "1000",
            },
            "metadata_env": {
                "access_token": "GOOGLE_ADS_ACCESS_TOKEN",
            },
        },
    ],
}


def write_example(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(EXAMPLE_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persistent Content Opportunity Radar collection service"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Validate config and initialize SQLite")
    init_parser.add_argument("--config", required=True)

    run_parser = sub.add_parser("run-once", help="Run all jobs that are due")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--job", action="append", dest="jobs")

    status_parser = sub.add_parser("status", help="Show collector state")
    status_parser.add_argument("--config", required=True)

    loop_parser = sub.add_parser("loop", help="Continuously run due jobs")
    loop_parser.add_argument("--config", required=True)
    loop_parser.add_argument("--poll-seconds", type=int, default=30)

    example_parser = sub.add_parser(
        "example-config",
        help="Write a safe example collection plan",
    )
    example_parser.add_argument("--output", default="collection-plan.example.json")

    args = parser.parse_args()

    if args.command == "example-config":
        write_example(args.output)
        print(args.output)
        return 0

    config = CollectorConfig.load(args.config)
    service = CollectionService(config)

    if args.command == "init":
        with RadarStore(config.database) as store:
            result = {
                "database": config.database,
                "job_count": len(config.jobs),
                "counts": store.counts(),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "status":
        print(json.dumps(service.status(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-once":
        result = service.run_once(
            force=args.force,
            job_ids=args.jobs,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "loop":
        poll = max(5, args.poll_seconds)
        try:
            while True:
                result = service.run_once()
                print(
                    json.dumps(
                        {
                            "at": isoformat(utcnow()),
                            **result,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                time.sleep(poll)
        except KeyboardInterrupt:
            return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
