"""Shared task/status/artifact protocol for AI Team Orchestrator V0.1."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ACKNOWLEDGED = "acknowledged"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


@dataclass(slots=True)
class TaskRequest:
    agent_id: str
    title: str
    prompt: str
    workspace: str = "."
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Artifact:
    artifact_id: str
    task_id: str
    kind: str
    path: str
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = iso(self.created_at)
        return data


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    agent_id: str
    title: str
    prompt: str
    workspace: str
    status: TaskStatus
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    acknowledged_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None

    @property
    def started(self) -> bool:
        return self.started_at is not None

    def to_dict(self, *, include_prompt: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "workspace": self.workspace,
            "status": self.status.value,
            "created_at": iso(self.created_at),
            "acknowledged_at": iso(self.acknowledged_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "started": self.started,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "error": self.error,
            "metadata": self.metadata,
        }
        if include_prompt:
            data["prompt"] = self.prompt
        return data


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {
        TaskStatus.ACKNOWLEDGED,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.ACKNOWLEDGED: {
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.BLOCKED,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}
