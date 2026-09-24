"""Local Agent Bridge for DSH.

Security and truth properties:
- binds to 127.0.0.1 by default;
- worker executable is configured at bridge startup, never by task payload;
- subprocess uses shell=False;
- workspace is constrained to one configured workspace root;
- task remains queued until worker emits an ACK/running protocol event;
- a process that exits 0 without ACK is still failed/not-started.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from adapters import AgentAdapter
from dispatcher import Dispatcher
from protocol import Artifact, TaskRecord, TaskRequest, TaskStatus, TERMINAL_STATUSES
from registry import AgentRegistry
from store import TaskStore


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


def _contains_sensitive_metadata(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _sensitive_key(key) or _contains_sensitive_metadata(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_metadata(item) for item in value)
    return False


def _safe_workspace(root: Path, requested: str) -> Path:
    raw = str(requested or ".").strip() or "."
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / candidate).resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("workspace must stay inside configured workspace root") from exc
    return resolved


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass(slots=True)
class BridgeConfig:
    database: Path
    state_dir: Path
    workspace_root: Path
    command: list[str]
    host: str = "127.0.0.1"
    port: int = 8765
    allow_pull_workers: bool = True
    worker_ttl_seconds: int = 90

    @property
    def configured(self) -> bool:
        """Whether push-local execution is configured."""
        return bool(self.command)

    @property
    def queueable(self) -> bool:
        return self.configured or self.allow_pull_workers


class DSHAdapter(AgentAdapter):
    agent_id = "dsh"

    def __init__(self, config: BridgeConfig, store: TaskStore) -> None:
        self.config = config
        self.store = store
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._completion_signals: set[str] = set()
        self._lock = threading.RLock()

    def submit(self, request: TaskRequest) -> str:
        if request.agent_id != self.agent_id:
            raise ValueError(f"DSH adapter cannot submit agent {request.agent_id}")
        if not self.config.queueable:
            raise RuntimeError("DSH has no configured execution transport")
        if _contains_sensitive_metadata(request.metadata):
            raise ValueError("task metadata must not contain credential-shaped fields")

        workspace = _safe_workspace(self.config.workspace_root, request.workspace)
        request = TaskRequest(
            agent_id=request.agent_id,
            title=request.title.strip(),
            prompt=request.prompt,
            workspace=str(workspace),
            metadata=request.metadata,
        )
        if not request.title:
            raise ValueError("task title is required")
        if not request.prompt.strip():
            raise ValueError("task prompt is required")

        record = self.store.create_task(request)
        task_dir = self._task_dir(record.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            task_dir / "request.json",
            {
                "task_id": record.task_id,
                "agent_id": record.agent_id,
                "title": record.title,
                "prompt": record.prompt,
                "workspace": record.workspace,
                "metadata": record.metadata,
                "protocol": {
                    "stdout_jsonl_events": [
                        {"type": "ack"},
                        {"type": "status", "status": "running"},
                        {"type": "artifact", "path": "relative/path", "kind": "file"},
                        {"type": "status", "status": "completed"},
                    ]
                },
                "message_file": str(task_dir / "messages.jsonl"),
            },
        )
        if self.config.configured:
            # Reserve push-local tasks so registered pull workers cannot race
            # the local subprocess before it emits its first ACK.
            self.store.reserve_task(record.task_id, "push-local:dsh")
            thread = threading.Thread(
                target=self._run_task,
                args=(record.task_id,),
                name=f"dsh-{record.task_id}",
                daemon=True,
            )
            with self._lock:
                self._threads[record.task_id] = thread
            thread.start()
        # In pull-only mode the task deliberately remains queued/not-started
        # until a registered worker leases it.
        return record.task_id

    def status(self, task_id: str) -> TaskRecord:
        return self.store.get_task(task_id)

    def message(self, task_id: str, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        task = self.store.get_task(task_id)
        if task.status in TERMINAL_STATUSES:
            raise ValueError(f"cannot message terminal task: {task.status.value}")
        self.store.add_message(task_id, "supervisor", text)
        message_file = self._task_dir(task_id) / "messages.jsonl"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        with message_file.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"role": "supervisor", "content": text},
                    ensure_ascii=False,
                )
                + "\n"
            )

    def artifacts(self, task_id: str) -> list[Artifact]:
        return self.store.artifacts(task_id)

    def cancel(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.status in TERMINAL_STATUSES:
            return task
        with self._lock:
            process = self._processes.get(task_id)
        if process and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        cancelled = self.store.transition(task_id, TaskStatus.CANCELLED)
        self.wait(task_id, timeout=3.0)
        return cancelled

    def wait(self, task_id: str, timeout: float | None = None) -> TaskRecord:
        with self._lock:
            thread = self._threads.get(task_id)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return self.store.get_task(task_id)

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._lock:
            task_ids = list(self._threads)
        for task_id in task_ids:
            try:
                task = self.store.get_task(task_id)
            except Exception:
                continue
            if task.status not in TERMINAL_STATUSES:
                try:
                    self.cancel(task_id)
                except Exception:
                    pass
        with self._lock:
            threads = list(self._threads.values())
        deadline = None if timeout is None else __import__("time").monotonic() + timeout
        for thread in threads:
            if thread is threading.current_thread():
                continue
            remaining = None if deadline is None else max(
                0.0,
                deadline - __import__("time").monotonic(),
            )
            thread.join(timeout=remaining)

    def _task_dir(self, task_id: str) -> Path:
        return self.config.state_dir / "tasks" / task_id

    def _command(self, task: TaskRecord) -> list[str]:
        task_dir = self._task_dir(task.task_id)
        substitutions = {
            "request_file": str(task_dir / "request.json"),
            "task_dir": str(task_dir),
            "workspace": task.workspace,
            "task_id": task.task_id,
        }
        return [token.format(**substitutions) for token in self.config.command]

    def _artifact_reference(self, task: TaskRecord, raw_path: str) -> str:
        requested = Path(str(raw_path or "").strip())
        workspace = Path(task.workspace).resolve()
        task_dir = self._task_dir(task.task_id).resolve()
        candidate = requested.resolve() if requested.is_absolute() else (workspace / requested).resolve()

        try:
            rel = candidate.relative_to(workspace)
            return f"workspace/{rel.as_posix()}"
        except ValueError:
            pass
        try:
            rel = candidate.relative_to(task_dir)
            return f"task/{rel.as_posix()}"
        except ValueError as exc:
            raise ValueError("artifact path is outside allowed task/workspace roots") from exc

    def _handle_event(self, task_id: str, event: Mapping[str, Any]) -> None:
        event_type = str(event.get("type") or "").strip().casefold()
        current = self.store.get_task(task_id)

        if event_type == "ack":
            if current.status == TaskStatus.QUEUED:
                self.store.transition(task_id, TaskStatus.ACKNOWLEDGED)
            return

        if event_type == "status":
            raw = str(event.get("status") or "").strip().casefold()
            try:
                status = TaskStatus(raw)
            except ValueError:
                return

            current = self.store.get_task(task_id)
            if status == TaskStatus.RUNNING and current.status == TaskStatus.QUEUED:
                self.store.transition(task_id, TaskStatus.ACKNOWLEDGED)
                self.store.transition(task_id, TaskStatus.RUNNING)
                return
            if status == TaskStatus.RUNNING and current.status in {
                TaskStatus.ACKNOWLEDGED,
                TaskStatus.BLOCKED,
            }:
                self.store.transition(task_id, TaskStatus.RUNNING)
                return
            if status == TaskStatus.BLOCKED and current.status in {
                TaskStatus.ACKNOWLEDGED,
                TaskStatus.RUNNING,
            }:
                self.store.transition(task_id, TaskStatus.BLOCKED)
                return
            if status == TaskStatus.COMPLETED and current.status == TaskStatus.RUNNING:
                # Completion is provisional until the worker exits successfully.
                with self._lock:
                    self._completion_signals.add(task_id)
                return
            if status == TaskStatus.FAILED and current.status not in TERMINAL_STATUSES:
                if current.status == TaskStatus.QUEUED:
                    self.store.transition(
                        task_id,
                        TaskStatus.FAILED,
                        error=str(event.get("error") or "worker reported failure before ACK"),
                    )
                else:
                    self.store.transition(
                        task_id,
                        TaskStatus.FAILED,
                        error=str(event.get("error") or "worker reported failure"),
                    )
                return

        if event_type == "artifact":
            current = self.store.get_task(task_id)
            if current.status == TaskStatus.QUEUED:
                # Artifacts before ACK do not establish task start.
                return
            raw_path = str(event.get("path") or "").strip()
            if not raw_path:
                return
            reference = self._artifact_reference(current, raw_path)
            self.store.add_artifact(
                task_id,
                kind=str(event.get("kind") or "file"),
                path=reference,
                label=str(event.get("label") or ""),
                metadata={
                    key: value
                    for key, value in dict(event.get("metadata") or {}).items()
                    if not _sensitive_key(key)
                },
            )

    def _run_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        task_dir = self._task_dir(task_id)
        stdout_log = task_dir / "stdout.log"
        stderr_log = task_dir / "stderr.log"

        try:
            process = subprocess.Popen(
                self._command(task),
                cwd=task.workspace,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=False,
                env=os.environ.copy(),
            )
        except Exception as exc:
            self.store.transition(
                task_id,
                TaskStatus.FAILED,
                error=f"worker launch failed: {type(exc).__name__}: {exc}",
            )
            return

        with self._lock:
            self._processes[task_id] = process
        self.store.set_pid(task_id, process.pid)

        def drain_stderr() -> None:
            if process.stderr is None:
                return
            with stderr_log.open("a", encoding="utf-8") as log:
                for line in process.stderr:
                    log.write(line)
                    log.flush()

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()

        try:
            if process.stdout is not None:
                with stdout_log.open("a", encoding="utf-8") as log:
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        stripped = line.strip()
                        if not stripped.startswith("{"):
                            continue
                        try:
                            event = json.loads(stripped)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, Mapping):
                            try:
                                self._handle_event(task_id, event)
                            except Exception as exc:
                                log.write(f"[bridge-event-error] {type(exc).__name__}: {exc}\n")

            exit_code = process.wait()
            stderr_thread.join(timeout=2)
            current = self.store.get_task(task_id)

            if current.status == TaskStatus.CANCELLED:
                return
            if current.status == TaskStatus.QUEUED:
                self.store.transition(
                    task_id,
                    TaskStatus.FAILED,
                    exit_code=exit_code,
                    error="worker exited without ACK; task never started",
                )
                return
            if current.status == TaskStatus.FAILED:
                return

            with self._lock:
                completion_signaled = task_id in self._completion_signals

            if exit_code == 0 and completion_signaled and current.status == TaskStatus.RUNNING:
                self.store.transition(
                    task_id,
                    TaskStatus.COMPLETED,
                    exit_code=exit_code,
                )
                return

            if exit_code == 0:
                self.store.transition(
                    task_id,
                    TaskStatus.FAILED,
                    exit_code=exit_code,
                    error="worker exited without completed status event",
                )
            else:
                self.store.transition(
                    task_id,
                    TaskStatus.FAILED,
                    exit_code=exit_code,
                    error=f"worker exited with code {exit_code}",
                )
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            with self._lock:
                self._processes.pop(task_id, None)
                self._completion_signals.discard(task_id)
                self._threads.pop(task_id, None)


def _json_body(handler: BaseHTTPRequestHandler, *, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except ValueError as exc:
        raise ValueError("invalid Content-Length") from exc
    if length <= 0:
        return {}
    if length > max_bytes:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


def create_handler(
    *,
    dispatcher: Dispatcher,
    store: TaskStore,
    registry: AgentRegistry,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AITeamAgentBridge/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message}})

        def do_GET(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if path == "/health":
                    self._json(
                        200,
                        {
                            "status": "ok",
                            "dsh_configured": registry.get("dsh").configured,
                            "tasks": len(store.list_tasks(limit=500)),
                        },
                    )
                    return
                if path == "/agents":
                    self._json(200, {"items": [agent.to_dict() for agent in registry.list()]})
                    return
                if path == "/tasks":
                    self._json(
                        200,
                        {"items": [task.to_dict() for task in store.list_tasks()]},
                    )
                    return
                if path.startswith("/tasks/"):
                    suffix = path[len("/tasks/"):]
                    parts = suffix.split("/")
                    task_id = unquote(parts[0])
                    task = store.get_task(task_id)
                    if len(parts) == 1:
                        payload = task.to_dict()
                        payload["messages"] = store.messages(task_id)
                        self._json(200, payload)
                        return
                    if len(parts) == 2 and parts[1] == "artifacts":
                        self._json(
                            200,
                            {"items": [item.to_dict() for item in store.artifacts(task_id)]},
                        )
                        return
                self._error(404, "not_found", "endpoint not found")
            except KeyError:
                self._error(404, "not_found", "task/agent not found")
            except Exception as exc:
                self._error(400, "bad_request", str(exc))

        def do_POST(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if path == "/tasks":
                    body = _json_body(self)
                    agent_id = str(body.get("agent_id") or "").strip()
                    request = TaskRequest(
                        agent_id=agent_id,
                        title=str(body.get("title") or ""),
                        prompt=str(body.get("prompt") or ""),
                        workspace=str(body.get("workspace") or "."),
                        metadata=dict(body.get("metadata") or {}),
                    )
                    adapter = dispatcher.adapter(agent_id)
                    task_id = adapter.submit(request)
                    self._json(202, store.get_task(task_id).to_dict())
                    return

                if path.startswith("/tasks/"):
                    suffix = path[len("/tasks/"):]
                    parts = suffix.split("/")
                    task_id = unquote(parts[0])
                    task = store.get_task(task_id)
                    adapter = dispatcher.adapter(task.agent_id)

                    if len(parts) == 2 and parts[1] == "messages":
                        body = _json_body(self)
                        adapter.message(task_id, str(body.get("message") or ""))
                        self._json(202, store.get_task(task_id).to_dict())
                        return

                    if len(parts) == 2 and parts[1] == "cancel":
                        self._json(200, adapter.cancel(task_id).to_dict())
                        return

                self._error(404, "not_found", "endpoint not found")
            except KeyError:
                self._error(404, "not_found", "task/agent not found")
            except RuntimeError as exc:
                self._error(503, "unavailable", str(exc))
            except ValueError as exc:
                self._error(400, "bad_request", str(exc))
            except Exception:
                self._error(500, "internal_error", "internal server error")

    return Handler


def load_command(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    # JSON argv is preferred because it preserves token boundaries exactly.
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError("DSH command JSON must be an array of strings")
        return list(parsed)
    return shlex.split(raw)


def serve(config: BridgeConfig) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    store = TaskStore(config.database)
    dsh = DSHAdapter(config, store)
    registry = AgentRegistry.default(
        dsh_configured=config.queueable,
        dsh_available=config.configured,
        dsh_queueable=config.queueable,
    )
    dispatcher = Dispatcher(
        registry=registry,
        adapters={"dsh": dsh},
    )
    server = ThreadingHTTPServer(
        (config.host, config.port),
        create_handler(
            dispatcher=dispatcher,
            store=store,
            registry=registry,
            dsh=dsh,
            config=config,
        ),
    )
    print(
        json.dumps(
            {
                "status": "listening",
                "host": config.host,
                "port": config.port,
                "dsh_push_configured": config.configured,
                "pull_workers_enabled": config.allow_pull_workers,
                "workspace_root": str(config.workspace_root),
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
        dsh.shutdown()
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team local DSH agent bridge")
    parser.add_argument("--db", default=".orchestrator/tasks.db")
    parser.add_argument("--state-dir", default=".orchestrator")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-pull-workers",
        action="store_true",
        help="Disable registered pull-worker queueing",
    )
    parser.add_argument(
        "--worker-ttl-seconds",
        type=int,
        default=90,
    )
    parser.add_argument(
        "--dsh-command",
        default=os.getenv("DSH_COMMAND_JSON") or os.getenv("DSH_COMMAND") or "",
        help="Configured DSH argv (JSON array preferred); supports {request_file}, {task_dir}, {workspace}, {task_id}",
    )
    args = parser.parse_args()

    config = BridgeConfig(
        database=Path(args.db),
        state_dir=Path(args.state_dir),
        workspace_root=Path(args.workspace_root).resolve(),
        command=load_command(args.dsh_command),
        host=args.host,
        port=max(1, min(int(args.port), 65535)),
        allow_pull_workers=not args.no_pull_workers,
        worker_ttl_seconds=max(10, min(int(args.worker_ttl_seconds), 3600)),
    )
    serve(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
