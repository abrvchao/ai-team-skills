"""Persistent registered worker daemon for AI Team Orchestrator.

Run this once on the machine that can execute the real agent. The Supervisor does
not need to know the agent executable; tasks are leased through the bridge.

Example:

    export DSH_INNER_COMMAND_JSON='[
      "/absolute/path/to/dsh",
      "run",
      "--prompt-file",
      "{prompt_file}"
    ]'

    python pull_worker.py \
      --bridge http://127.0.0.1:8765 \
      --agent-id dsh \
      --label "DSH Mac worker"

The inner command is fixed at worker startup and is never supplied by a task.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def load_argv(raw: str) -> list[str]:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("worker command is not configured")
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) for item in parsed):
        raise ValueError("worker command must be a non-empty JSON array of strings")
    return list(parsed)


class WorkerBridgeClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.worker_id: str | None = None
        self.token: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = False,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise RuntimeError("worker is not registered")
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("error", {}).get("message")
            except Exception:
                message = None
            raise RuntimeError(message or f"bridge HTTP {exc.code}") from exc

    def register(
        self,
        *,
        agent_id: str,
        label: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/workers/register",
            {
                "agent_id": agent_id,
                "label": label,
                "capabilities": capabilities,
            },
        )
        self.worker_id = payload["worker_id"]
        self.token = payload["token"]
        return payload

    def heartbeat(self) -> dict[str, Any]:
        if not self.worker_id:
            raise RuntimeError("worker is not registered")
        return self._request(
            "POST",
            f"/workers/{urllib.parse.quote(self.worker_id)}/heartbeat",
            {},
            auth=True,
        )

    def lease(self) -> dict[str, Any] | None:
        if not self.worker_id:
            raise RuntimeError("worker is not registered")
        payload = self._request(
            "POST",
            f"/workers/{urllib.parse.quote(self.worker_id)}/lease",
            {},
            auth=True,
        )
        return payload.get("task")

    def event(self, task_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/tasks/{urllib.parse.quote(task_id)}/events",
            event,
            auth=True,
        )

    def messages(self, task_id: str) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/tasks/{urllib.parse.quote(task_id)}/messages",
            auth=True,
        )
        return list(payload.get("messages") or [])

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/tasks/{urllib.parse.quote(task_id)}",
        )


class PullWorker:
    def __init__(
        self,
        *,
        bridge_url: str,
        agent_id: str,
        label: str,
        command: list[str],
        poll_seconds: float = 2.0,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        if not command:
            raise ValueError("worker command is required before registration")
        self.client = WorkerBridgeClient(bridge_url)
        self.agent_id = agent_id
        self.label = label
        self.command = list(command)
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.heartbeat_seconds = max(1.0, float(heartbeat_seconds))

    def register(self) -> dict[str, Any]:
        return self.client.register(
            agent_id=self.agent_id,
            label=self.label,
            capabilities=["implementation", "testing", "debugging", "git"],
        )

    def _task_files(self, task: dict[str, Any]) -> dict[str, Path]:
        workspace = Path(task["workspace"]).resolve()
        task_dir = workspace / ".ai-team-worker" / task["task_id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "task_dir": task_dir,
            "request_file": task_dir / "request.json",
            "prompt_file": task_dir / "prompt.txt",
            "message_file": task_dir / "messages.jsonl",
            "stdout_file": task_dir / "stdout.log",
            "stderr_file": task_dir / "stderr.log",
            "artifact_manifest": task_dir / "artifacts.jsonl",
        }
        files["request_file"].write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        files["prompt_file"].write_text(
            f"# Task\n{task.get('title', '')}\n\n"
            f"{task.get('prompt', '')}\n\n"
            "# Runtime contract\n"
            f"Follow-up messages are mirrored to: {files['message_file']}\n"
            f"Optional artifact JSONL may be appended to: {files['artifact_manifest']}\n",
            encoding="utf-8",
        )
        self._write_messages(files["message_file"], task.get("messages") or [])
        return files

    def _write_messages(self, path: Path, messages: list[dict[str, Any]]) -> None:
        path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in messages),
            encoding="utf-8",
        )

    def _command(self, task: dict[str, Any], files: dict[str, Path]) -> list[str]:
        values = {
            "task_id": task["task_id"],
            "workspace": task["workspace"],
            **{key: str(value) for key, value in files.items()},
        }
        return [token.format(**values) for token in self.command]

    def _send_artifact(
        self,
        task_id: str,
        *,
        path: Path,
        kind: str = "file",
        label: str = "",
    ) -> None:
        self.client.event(
            task_id,
            {
                "type": "artifact",
                "path": str(path),
                "kind": kind,
                "label": label,
            },
        )

    def _manifest_artifacts(self, task_id: str, manifest: Path) -> None:
        if not manifest.is_file():
            return
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or not item.get("path"):
                continue
            event = {
                "type": "artifact",
                "path": str(item["path"]),
                "kind": str(item.get("kind") or "file"),
                "label": str(item.get("label") or ""),
                "metadata": dict(item.get("metadata") or {}),
            }
            self.client.event(task_id, event)

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["task_id"]
        files = self._task_files(task)
        argv = self._command(task, files)

        stdout_handle = files["stdout_file"].open("w", encoding="utf-8")
        stderr_handle = files["stderr_file"].open("w", encoding="utf-8")
        try:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=task["workspace"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    shell=False,
                    env=os.environ.copy(),
                )
            except Exception as exc:
                return self.client.event(
                    task_id,
                    {
                        "type": "status",
                        "status": "failed",
                        "error": f"agent launch failed: {type(exc).__name__}: {exc}",
                    },
                )

            # A real child process now exists: execution has actually started.
            self.client.event(
                task_id,
                {"type": "status", "status": "running"},
            )

            last_heartbeat = 0.0
            last_messages: list[dict[str, Any]] = list(task.get("messages") or [])
            while process.poll() is None:
                now = time.monotonic()
                if now - last_heartbeat >= self.heartbeat_seconds:
                    self.client.heartbeat()
                    last_heartbeat = now

                current = self.client.task(task_id)
                if current.get("status") == "cancelled":
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    return current

                messages = self.client.messages(task_id)
                if messages != last_messages:
                    self._write_messages(files["message_file"], messages)
                    last_messages = messages

                time.sleep(self.poll_seconds)

            exit_code = process.wait()
        finally:
            stdout_handle.close()
            stderr_handle.close()

        # Logs are always useful review artifacts for work that really started.
        self._send_artifact(
            task_id,
            path=files["stdout_file"],
            label="worker stdout",
        )
        self._send_artifact(
            task_id,
            path=files["stderr_file"],
            label="worker stderr",
        )
        self._manifest_artifacts(task_id, files["artifact_manifest"])

        if exit_code == 0:
            return self.client.event(
                task_id,
                {"type": "status", "status": "completed"},
            )
        return self.client.event(
            task_id,
            {
                "type": "status",
                "status": "failed",
                "error": f"agent process exited with code {exit_code}",
            },
        )

    def run_once(self) -> dict[str, Any] | None:
        task = self.client.lease()
        if task is None:
            self.client.heartbeat()
            return None
        return self.execute(task)

    def run_forever(self) -> None:
        self.register()
        while True:
            task = self.client.lease()
            if task is None:
                self.client.heartbeat()
                time.sleep(self.poll_seconds)
                continue
            self.execute(task)


def main() -> int:
    parser = argparse.ArgumentParser(description="Registered AI Team pull worker")
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    parser.add_argument("--agent-id", default="dsh")
    parser.add_argument("--label", default="DSH local worker")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--command-json",
        default=os.getenv("DSH_INNER_COMMAND_JSON") or "",
        help="Fixed local agent argv as a JSON array of strings",
    )
    args = parser.parse_args()

    worker = PullWorker(
        bridge_url=args.bridge,
        agent_id=args.agent_id,
        label=args.label,
        command=load_argv(args.command_json),
        poll_seconds=args.poll_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    worker.register()
    if args.once:
        worker.run_once()
        return 0
    while True:
        task = worker.client.lease()
        if task is None:
            worker.client.heartbeat()
            time.sleep(worker.poll_seconds)
            continue
        worker.execute(task)


if __name__ == "__main__":
    raise SystemExit(main())
