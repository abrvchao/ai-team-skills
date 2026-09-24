"""Thin wrapper that adapts an arbitrary local DSH CLI to bridge protocol.

The wrapped DSH CLI does not need to understand the orchestrator protocol.
This process owns ACK/running/completed/failed events and runs the configured
inner command with shell=False.

Configure inner argv with DSH_INNER_COMMAND_JSON, for example:

    ["dsh", "run", "--prompt-file", "{prompt_file}"]

Supported placeholders per argv token:
    {request_file}
    {prompt_file}
    {workspace}
    {task_dir}
    {task_id}
    {message_file}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def load_argv(raw: str) -> list[str]:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("DSH inner command is not configured")
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) for item in parsed):
        raise ValueError("DSH_INNER_COMMAND_JSON must be a non-empty JSON array of strings")
    return list(parsed)


def pump(stream, target, prefix: str) -> None:
    if stream is None:
        return
    for line in stream:
        target.write(f"{prefix}{line}")
        target.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt local DSH CLI to AI Team protocol")
    parser.add_argument("request_file")
    parser.add_argument(
        "--inner-command-json",
        default=os.getenv("DSH_INNER_COMMAND_JSON") or "",
    )
    args = parser.parse_args()

    request_file = Path(args.request_file).resolve()
    request = json.loads(request_file.read_text(encoding="utf-8"))
    workspace = Path(request["workspace"]).resolve()
    task_dir = request_file.parent
    task_id = str(request["task_id"])
    message_file = Path(request.get("message_file") or (task_dir / "messages.jsonl"))

    prompt_file = task_dir / "prompt.txt"
    prompt_file.write_text(
        f"# Task\n{request.get('title', '')}\n\n"
        f"{request.get('prompt', '')}\n\n"
        f"# Follow-up messages\nRead JSONL from: {message_file}\n",
        encoding="utf-8",
    )

    substitutions = {
        "request_file": str(request_file),
        "prompt_file": str(prompt_file),
        "workspace": str(workspace),
        "task_dir": str(task_dir),
        "task_id": task_id,
        "message_file": str(message_file),
    }
    argv = [
        token.format(**substitutions)
        for token in load_argv(args.inner_command_json)
    ]

    emit({"type": "ack"})
    emit({"type": "status", "status": "running"})

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=False,
            env=os.environ.copy(),
        )
    except Exception as exc:
        emit(
            {
                "type": "status",
                "status": "failed",
                "error": f"DSH launch failed: {type(exc).__name__}: {exc}",
            }
        )
        return 127

    out_thread = threading.Thread(
        target=pump,
        args=(process.stdout, sys.stderr, "[dsh:stdout] "),
        daemon=True,
    )
    err_thread = threading.Thread(
        target=pump,
        args=(process.stderr, sys.stderr, "[dsh:stderr] "),
        daemon=True,
    )
    out_thread.start()
    err_thread.start()

    exit_code = process.wait()
    out_thread.join(timeout=2)
    err_thread.join(timeout=2)

    if exit_code == 0:
        emit({"type": "status", "status": "completed"})
        return 0

    emit(
        {
            "type": "status",
            "status": "failed",
            "error": f"DSH process exited with code {exit_code}",
        }
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
