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
import signal
import string
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


def render_argv(raw: str, substitutions: dict[str, str]) -> list[str]:
    """仅接受事先登记的占位符；未知占位符或非法格式直接拒绝。"""
    formatter = string.Formatter()
    result = []
    for token in load_argv(raw):
        for _, field, spec, conversion in formatter.parse(token):
            if field is not None and (field not in substitutions or spec or conversion):
                raise ValueError("unsupported_dsh_command_placeholder")
        result.append(formatter.vformat(token, (), substitutions))
    return result


def child_environment() -> dict[str, str]:
    """DSH 子进程只获得明确允许的非秘密变量。"""
    allowed = {
        "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER", "LOGNAME",
        "DSH_HOME", "DSH_PROFILE", "DSH_NODE_BIN", "DSH_BIN", "DSH_MODEL_BACKEND",
        "DSH_FREE_PREFLIGHT_DIR", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def wrapper_timeout() -> int:
    """本地进程硬超时，不能无限占用 Worker 槽位。"""
    try:
        seconds = int(os.getenv("DSH_WRAPPER_TIMEOUT_SECONDS", "600"))
    except ValueError as exc:
        raise ValueError("invalid_dsh_wrapper_timeout") from exc
    if not 1 <= seconds <= 3600:
        raise ValueError("invalid_dsh_wrapper_timeout")
    return seconds


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

    try:
        request_file = Path(args.request_file).resolve()
        request = json.loads(request_file.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or not isinstance(request.get("workspace"), str) \
                or not request["workspace"].strip() or not isinstance(request.get("task_id"), str) \
                or not request["task_id"].strip():
            raise ValueError("invalid_dsh_request_fields")
        workspace = Path(request["workspace"]).resolve()
        task_dir = request_file.parent
        task_id = request["task_id"]
        message_file = Path(request.get("message_file") or (task_dir / "messages.jsonl"))
        prompt_file = task_dir / "prompt.txt"
        substitutions = {
            "request_file": str(request_file), "prompt_file": str(prompt_file),
            "workspace": str(workspace), "task_dir": str(task_dir),
            "task_id": task_id, "message_file": str(message_file),
        }
        argv = render_argv(args.inner_command_json, substitutions)
        timeout_seconds = wrapper_timeout()
        prompt_file.write_text(
            f"# Task\n{request.get('title', '')}\n\n"
            f"{request.get('prompt', '')}\n\n"
            f"# Follow-up messages\nRead JSONL from: {message_file}\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # 不输出含敏感路径或凭据的原始异常文本。
        emit({"type": "status", "status": "failed",
              "error": f"invalid_dsh_request_or_config:{type(exc).__name__}"})
        return 2

    emit({"type": "ack"})

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
            env=child_environment(),
            start_new_session=True,
        )
    except Exception as exc:
        emit(
            {
                "type": "status",
                "status": "failed",
                # 原始异常可能包含含密钥的命令行参数，不能透传 Bridge。
                "error": f"DSH launch failed: {type(exc).__name__}",
            }
        )
        return 127

    # Only a successfully spawned inner DSH process proves actual execution.
    emit({"type": "status", "status": "running"})

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

    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        # killpg 确保内层 CLI 的子进程也被清理，而不是只结束父进程。
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # 父进程即使已经退出，同进程组的后代也可能仍在运行。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        out_thread.join(timeout=2)
        err_thread.join(timeout=2)
        emit({"type": "status", "status": "failed", "error": "dsh_wrapper_timeout"})
        return 124
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
