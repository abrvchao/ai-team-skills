"""Unified adapter interface used by Supervisor/Dispatcher."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from protocol import Artifact, TaskRecord, TaskRequest


class AgentAdapter(ABC):
    agent_id: str

    @abstractmethod
    def submit(self, request: TaskRequest) -> str:
        raise NotImplementedError

    @abstractmethod
    def status(self, task_id: str) -> TaskRecord:
        raise NotImplementedError

    @abstractmethod
    def message(self, task_id: str, message: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def artifacts(self, task_id: str) -> list[Artifact]:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, task_id: str) -> TaskRecord:
        raise NotImplementedError


class UnavailableAdapter(AgentAdapter):
    def __init__(self, agent_id: str, reason: str = "not configured") -> None:
        self.agent_id = agent_id
        self.reason = reason

    def _fail(self) -> None:
        raise RuntimeError(f"{self.agent_id} adapter unavailable: {self.reason}")

    def submit(self, request: TaskRequest) -> str:
        self._fail()

    def status(self, task_id: str) -> TaskRecord:
        self._fail()

    def message(self, task_id: str, message: str) -> None:
        self._fail()

    def artifacts(self, task_id: str) -> list[Artifact]:
        self._fail()

    def cancel(self, task_id: str) -> TaskRecord:
        self._fail()
