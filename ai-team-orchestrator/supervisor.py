"""Supervisor-side bridge client.

This is the control-plane surface future ChatGPT/Gemini/Grok integrations use.
It reports started=True only when the bridge has a worker-derived started_at.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SupervisorTaskView:
    task_id: str
    agent_id: str
    status: str
    started: bool
    started_at: str | None
    acknowledged_at: str | None
    finished_at: str | None
    artifacts: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "started": self.started,
            "started_at": self.started_at,
            "acknowledged_at": self.acknowledged_at,
            "finished_at": self.finished_at,
            "artifacts": self.artifacts,
        }


class BridgeClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("error", {}).get("message")
            except Exception:
                message = None
            raise RuntimeError(message or f"bridge HTTP {exc.code}") from exc

    def agents(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/agents").get("items") or [])

    def tasks(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/tasks").get("items") or [])

    def submit(
        self,
        *,
        agent_id: str,
        title: str,
        prompt: str,
        workspace: str = ".",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/tasks",
            {
                "agent_id": agent_id,
                "title": title,
                "prompt": prompt,
                "workspace": workspace,
                "metadata": metadata or {},
            },
        )

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tasks/{urllib.parse.quote(task_id)}")

    def message(self, task_id: str, message: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/tasks/{urllib.parse.quote(task_id)}/messages",
            {"message": message},
        )

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/tasks/{urllib.parse.quote(task_id)}/artifacts",
        )
        return list(payload.get("items") or [])

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/tasks/{urllib.parse.quote(task_id)}/cancel",
            {},
        )

    def view(self, task_id: str) -> SupervisorTaskView:
        task = self.task(task_id)
        return SupervisorTaskView(
            task_id=task["task_id"],
            agent_id=task["agent_id"],
            status=task["status"],
            started=bool(task.get("started")),
            started_at=task.get("started_at"),
            acknowledged_at=task.get("acknowledged_at"),
            finished_at=task.get("finished_at"),
            artifacts=self.artifacts(task_id),
        )


class Supervisor:
    def __init__(self, client: BridgeClient) -> None:
        self.client = client

    def dispatch(
        self,
        *,
        agent_id: str,
        title: str,
        prompt: str,
        workspace: str = ".",
        metadata: dict[str, Any] | None = None,
    ) -> SupervisorTaskView:
        task = self.client.submit(
            agent_id=agent_id,
            title=title,
            prompt=prompt,
            workspace=workspace,
            metadata=metadata,
        )
        return self.client.view(task["task_id"])

    def status(self, task_id: str) -> SupervisorTaskView:
        return self.client.view(task_id)
