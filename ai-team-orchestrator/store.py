"""Persistent SQLite task state for AI Team Orchestrator."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from protocol import (
    ALLOWED_TRANSITIONS,
    Artifact,
    TaskRecord,
    TaskRequest,
    TaskStatus,
    TERMINAL_STATUSES,
    utcnow,
)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "TaskStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    pid INTEGER,
                    exit_code INTEGER,
                    error TEXT,
                    metadata_json TEXT NOT NULL,
                    worker_id TEXT,
                    leased_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                ON tasks(status, created_at DESC, task_id DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    capabilities_json TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_workers_agent_heartbeat
                ON workers(agent_id, heartbeat_at DESC, worker_id ASC);

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    label TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                """
            )
            self._ensure_column("tasks", "worker_id", "TEXT")
            self._ensure_column("tasks", "leased_at", "TEXT")
            self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def create_task(self, request: TaskRequest) -> TaskRecord:
        now = utcnow()
        task_id = _stable_id(
            "task",
            request.agent_id,
            request.title,
            now.isoformat(),
        )
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO tasks (
                    task_id, agent_id, title, prompt, workspace, status,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    request.agent_id,
                    request.title,
                    request.prompt,
                    request.workspace,
                    TaskStatus.QUEUED.value,
                    _iso(now),
                    _json(request.metadata),
                ),
            )
            self.conn.commit()
        return self.get_task(task_id)

    def _row(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            title=row["title"],
            prompt=row["prompt"],
            workspace=row["workspace"],
            status=TaskStatus(row["status"]),
            created_at=_dt(row["created_at"]) or utcnow(),
            acknowledged_at=_dt(row["acknowledged_at"]),
            started_at=_dt(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
            pid=row["pid"],
            exit_code=row["exit_code"],
            error=row["error"],
            metadata=dict(_loads(row["metadata_json"], {})),
        )

    def get_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            raise KeyError(task_id)
        return self._row(row)

    def list_tasks(self, limit: int = 100) -> list[TaskRecord]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM tasks
                ORDER BY created_at DESC, task_id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def transition(
        self,
        task_id: str,
        new_status: TaskStatus,
        *,
        pid: int | None = None,
        exit_code: int | None = None,
        error: str | None = None,
        event_time: datetime | None = None,
    ) -> TaskRecord:
        now = event_time or utcnow()
        with self._lock:
            current = self.get_task(task_id)
            if new_status == current.status:
                return current
            if new_status not in ALLOWED_TRANSITIONS[current.status]:
                raise ValueError(
                    f"invalid task transition {current.status.value} -> {new_status.value}"
                )

            acknowledged_at = current.acknowledged_at
            started_at = current.started_at
            finished_at = current.finished_at

            if new_status == TaskStatus.ACKNOWLEDGED:
                acknowledged_at = acknowledged_at or now
            elif new_status == TaskStatus.RUNNING:
                # Running is a worker event and therefore also proves ACK.
                acknowledged_at = acknowledged_at or now
                started_at = started_at or now
            elif new_status in TERMINAL_STATUSES:
                finished_at = now

            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    acknowledged_at = ?,
                    started_at = ?,
                    finished_at = ?,
                    pid = COALESCE(?, pid),
                    exit_code = COALESCE(?, exit_code),
                    error = COALESCE(?, error)
                WHERE task_id = ?
                """,
                (
                    new_status.value,
                    _iso(acknowledged_at),
                    _iso(started_at),
                    _iso(finished_at),
                    pid,
                    exit_code,
                    error,
                    task_id,
                ),
            )
            self.conn.commit()
        return self.get_task(task_id)

    def set_pid(self, task_id: str, pid: int) -> TaskRecord:
        with self._lock:
            self.conn.execute(
                "UPDATE tasks SET pid = ? WHERE task_id = ?",
                (int(pid), task_id),
            )
            self.conn.commit()
        return self.get_task(task_id)

    def add_message(self, task_id: str, role: str, content: str) -> None:
        self.get_task(task_id)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO messages (task_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, role, content, _iso(utcnow())),
            )
            self.conn.commit()

    def messages(self, task_id: str) -> list[dict[str, Any]]:
        self.get_task(task_id)
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT role, content, created_at
                FROM messages
                WHERE task_id = ?
                ORDER BY message_id ASC
                """,
                (task_id,),
            ).fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def register_worker(
        self,
        *,
        agent_id: str,
        label: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        worker_id = "worker_" + secrets.token_hex(10)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO workers (
                    worker_id, agent_id, label, token_hash,
                    capabilities_json, registered_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worker_id,
                    agent_id,
                    str(label or "")[:160],
                    token_hash,
                    _json(list(capabilities or [])),
                    _iso(now),
                    _iso(now),
                ),
            )
            self.conn.commit()
        return {
            "worker_id": worker_id,
            "agent_id": agent_id,
            "label": str(label or "")[:160],
            "capabilities": list(capabilities or []),
            "registered_at": _iso(now),
            "heartbeat_at": _iso(now),
            "token": token,
        }

    def _worker_row(self, row: sqlite3.Row, *, now: datetime, ttl_seconds: int) -> dict[str, Any]:
        heartbeat = _dt(row["heartbeat_at"])
        age = (now - heartbeat).total_seconds() if heartbeat else float("inf")
        return {
            "worker_id": row["worker_id"],
            "agent_id": row["agent_id"],
            "label": row["label"],
            "capabilities": list(_loads(row["capabilities_json"], [])),
            "registered_at": row["registered_at"],
            "heartbeat_at": row["heartbeat_at"],
            "online": age <= max(1, int(ttl_seconds)),
        }

    def authenticate_worker(
        self,
        token: str,
        *,
        now: datetime | None = None,
        ttl_seconds: int = 90,
    ) -> dict[str, Any]:
        token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
        current = now or utcnow()
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM workers WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if not row:
            raise PermissionError("invalid worker token")
        return self._worker_row(row, now=current, ttl_seconds=ttl_seconds)

    def get_worker(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        ttl_seconds: int = 90,
    ) -> dict[str, Any]:
        current = now or utcnow()
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
        if not row:
            raise KeyError(worker_id)
        return self._worker_row(row, now=current, ttl_seconds=ttl_seconds)

    def list_workers(
        self,
        *,
        agent_id: str | None = None,
        now: datetime | None = None,
        ttl_seconds: int = 90,
    ) -> list[dict[str, Any]]:
        current = now or utcnow()
        with self._lock:
            if agent_id:
                rows = self.conn.execute(
                    """
                    SELECT * FROM workers
                    WHERE agent_id = ?
                    ORDER BY heartbeat_at DESC, worker_id ASC
                    """,
                    (agent_id,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM workers ORDER BY agent_id, heartbeat_at DESC, worker_id",
                ).fetchall()
        return [
            self._worker_row(row, now=current, ttl_seconds=ttl_seconds)
            for row in rows
        ]

    def heartbeat_worker(
        self,
        worker_id: str,
        *,
        event_time: datetime | None = None,
    ) -> dict[str, Any]:
        now = event_time or utcnow()
        with self._lock:
            cursor = self.conn.execute(
                "UPDATE workers SET heartbeat_at = ? WHERE worker_id = ?",
                (_iso(now), worker_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(worker_id)
            self.conn.commit()
        return self.get_worker(worker_id, now=now)

    def lease_task(
        self,
        *,
        worker_id: str,
        agent_id: str,
        event_time: datetime | None = None,
    ) -> TaskRecord | None:
        """Atomically claim the oldest queued task for one agent.

        Leasing is the worker ACK. It sets acknowledged_at/leased_at but never
        started_at; only a later running event proves actual execution.
        """
        now = event_time or utcnow()
        with self._lock:
            worker = self.get_worker(worker_id, now=now)
            if worker["agent_id"] != agent_id:
                raise PermissionError("worker is registered for a different agent")

            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    """
                    SELECT task_id
                    FROM tasks
                    WHERE agent_id = ?
                      AND status = ?
                      AND worker_id IS NULL
                    ORDER BY created_at ASC, task_id ASC
                    LIMIT 1
                    """,
                    (agent_id, TaskStatus.QUEUED.value),
                ).fetchone()
                if not row:
                    self.conn.commit()
                    return None

                cursor = self.conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?,
                        worker_id = ?,
                        leased_at = ?,
                        acknowledged_at = COALESCE(acknowledged_at, ?)
                    WHERE task_id = ?
                      AND status = ?
                      AND worker_id IS NULL
                    """,
                    (
                        TaskStatus.ACKNOWLEDGED.value,
                        worker_id,
                        _iso(now),
                        _iso(now),
                        row["task_id"],
                        TaskStatus.QUEUED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    self.conn.rollback()
                    return None
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return self.get_task(row["task_id"])

    def add_artifact(
        self,
        task_id: str,
        *,
        kind: str,
        path: str,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        self.get_task(task_id)
        now = utcnow()
        artifact_id = _stable_id("artifact", task_id, kind, path)
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (
                    artifact_id, task_id, kind, path, label,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    task_id,
                    kind,
                    path,
                    label,
                    _json(metadata or {}),
                    _iso(now),
                ),
            )
            self.conn.commit()
        return Artifact(
            artifact_id=artifact_id,
            task_id=task_id,
            kind=kind,
            path=path,
            label=label,
            metadata=metadata or {},
            created_at=now,
        )

    def artifacts(self, task_id: str) -> list[Artifact]:
        self.get_task(task_id)
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT *
                FROM artifacts
                WHERE task_id = ?
                ORDER BY created_at ASC, artifact_id ASC
                """,
                (task_id,),
            ).fetchall()
        return [
            Artifact(
                artifact_id=row["artifact_id"],
                task_id=row["task_id"],
                kind=row["kind"],
                path=row["path"],
                label=row["label"],
                metadata=dict(_loads(row["metadata_json"], {})),
                created_at=_dt(row["created_at"]) or utcnow(),
            )
            for row in rows
        ]
