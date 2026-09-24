"""Fake DSH worker implementing the bridge JSONL event protocol.

Used only for CI/demo. It proves the orchestrator transport and truth semantics
without pretending that a real DSH binary is configured.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_file")
    args = parser.parse_args()

    request_path = Path(args.request_file)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    metadata = dict(request.get("metadata") or {})
    mode = str(metadata.get("mode") or "success")

    if mode == "no_ack":
        time.sleep(float(metadata.get("sleep_seconds") or 0.2))
        print("worker exited without protocol ACK", flush=True)
        return 0

    emit({"type": "ack"})
    emit({"type": "status", "status": "running"})

    if mode == "block":
        emit({"type": "status", "status": "blocked"})
        time.sleep(float(metadata.get("sleep_seconds") or 0.2))
        emit({"type": "status", "status": "running"})

    if mode == "sleep":
        time.sleep(float(metadata.get("sleep_seconds") or 2.0))

    workspace = Path(request["workspace"])
    output_dir = workspace / ".orchestrator-demo"
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / f"{request['task_id']}.txt"
    artifact.write_text(
        "agent=DSH\n"
        f"task_id={request['task_id']}\n"
        f"title={request['title']}\n",
        encoding="utf-8",
    )
    emit(
        {
            "type": "artifact",
            "path": str(artifact),
            "kind": "file",
            "label": "fake DSH result",
        }
    )

    if mode == "fail":
        emit({"type": "status", "status": "failed", "error": "fake worker failure"})
        return 2

    emit({"type": "status", "status": "completed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
