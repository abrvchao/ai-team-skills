"""Android M2 Auto-E2E Watcher（安卓 M2 自动端到端监视器）.

只读等待已授权设备，等待 Android Worker 注册后自动提交 inspect_device 任务，
并验证 report + screenshot 成果。不会点击、输入或修改手机状态。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supervisor import BridgeClient

TERMINAL = {"completed", "failed", "cancelled"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    # 中文说明：监视器可能长期运行；状态必须原子写入，避免断电/重启留下半份 JSON。
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def authorized_devices(adb_bin: str) -> list[str]:
    """返回 state=device 的已授权设备序列号；unauthorized/offline 均不算可用。"""
    result = subprocess.run(
        [adb_bin, "devices"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    rows: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            rows.append(parts[0])
    return rows


def android_agent_ready(client: BridgeClient) -> bool:
    for item in client.agents():
        if item.get("agent_id") == "android":
            return bool(item.get("configured") and item.get("available") and item.get("queueable"))
    return False


def _artifact_path(raw_path: str, workspace: Path) -> Path:
    """把 Bridge 的安全 artifact reference（成果引用）还原为本机可验证路径。"""
    path = Path(raw_path)
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "workspace":
        # 中文说明：Bridge 会把 workspace 内绝对路径规范化为 workspace/<relative>；
        # 验收必须按本次提交的 workspace 还原，不能相对 watcher 当前目录猜路径。
        return workspace / Path(*parts[1:])
    return workspace / path


def validate_android_artifacts(client: BridgeClient, task_id: str, workspace: Path) -> dict[str, Any]:
    """验证真实 Bridge artifact，而不是仅相信 task completed。"""
    artifacts = client.artifacts(task_id)
    report_path: Path | None = None
    screenshot_path: Path | None = None
    for item in artifacts:
        path_value = str(item.get("path") or "")
        if not path_value:
            continue
        path = _artifact_path(path_value, workspace)
        if path.name == "android_report.json":
            report_path = path
        elif path.name == "android_screen.png":
            screenshot_path = path

    failures: list[str] = []
    report: dict[str, Any] = {}
    if report_path is None or not report_path.is_file():
        failures.append("android_report.json missing")
    else:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"android_report.json invalid: {type(exc).__name__}")

    if report:
        if report.get("mode") != "read_only":
            failures.append("mode is not read_only")
        if report.get("runtime") != "adb":
            failures.append("runtime is not adb")
        if not report.get("serial"):
            failures.append("device serial missing")
        if not report.get("android_version"):
            failures.append("android version missing")
        if report.get("screenshot_available") is not True:
            failures.append("screenshot_available is not true")

    if screenshot_path is None or not screenshot_path.is_file():
        failures.append("android_screen.png missing")
    elif screenshot_path.stat().st_size <= 100:
        failures.append("android_screen.png too small")

    return {
        "ok": not failures,
        "failures": failures,
        "report_path": str(report_path) if report_path else None,
        "screenshot_path": str(screenshot_path) if screenshot_path else None,
        "report": report,
        "artifacts": artifacts,
    }


def step(
    *,
    client: BridgeClient,
    state_path: Path,
    workspace: Path,
    adb_bin: str,
) -> dict[str, Any]:
    """执行一次状态推进；设计成可重入，LaunchAgent 重启也不会重复提交已存在任务。"""
    state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    if state.get("status") in {"passed", "failed"}:
        return state

    devices = authorized_devices(adb_bin)
    if not devices:
        state = {"status": "waiting_device", "updated_at": _utcnow(), "devices": []}
        _atomic_json(state_path, state)
        return state

    if not android_agent_ready(client):
        state = {"status": "waiting_worker", "updated_at": _utcnow(), "devices": devices}
        _atomic_json(state_path, state)
        return state

    task_id = state.get("task_id")
    if not task_id:
        workspace.mkdir(parents=True, exist_ok=True)
        task = client.submit(
            agent_id="android",
            title="M2 Android read-only E2E（M2 安卓只读端到端验收）",
            prompt=(
                "Inspect the connected authorized Android device in read-only mode. "
                "Capture device metadata and one screenshot. Do not tap, type, launch apps, or mutate device state."
            ),
            workspace=str(workspace),
            metadata={
                "action": "inspect_device",
                "m2_gate": "M2-ANDROID-001",
                "evidence_mode": "read_only",
            },
        )
        state = {
            "status": "submitted",
            "task_id": task["task_id"],
            "devices": devices,
            "submitted_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        _atomic_json(state_path, state)
        return state

    task = client.task(str(task_id))
    status = str(task.get("status") or "unknown")
    if status not in TERMINAL:
        state["status"] = "running" if task.get("started") else "submitted"
        state["bridge_status"] = status
        state["updated_at"] = _utcnow()
        _atomic_json(state_path, state)
        return state

    if status != "completed":
        state.update({
            "status": "failed",
            "bridge_status": status,
            "error": task.get("error") or f"Android E2E task ended as {status}",
            "finished_at": _utcnow(),
            "updated_at": _utcnow(),
        })
        _atomic_json(state_path, state)
        return state

    validation = validate_android_artifacts(client, str(task_id), workspace)
    state.update({
        "status": "passed" if validation["ok"] else "failed",
        "bridge_status": status,
        "validation": validation,
        "finished_at": _utcnow(),
        "updated_at": _utcnow(),
    })
    _atomic_json(state_path, state)
    return state


def watch(*, bridge_url: str, state_path: Path, workspace: Path, adb_bin: str, interval: float) -> int:
    client = BridgeClient(bridge_url, timeout=10)
    while True:
        try:
            state = step(client=client, state_path=state_path, workspace=workspace, adb_bin=adb_bin)
        except Exception as exc:
            # 中文说明：Bridge 暂时不可用不应让长期 watcher 永久退出；保留错误后继续等待。
            state = {"status": "waiting_bridge", "error": f"{type(exc).__name__}: {exc}", "updated_at": _utcnow()}
            _atomic_json(state_path, state)
        if state.get("status") == "passed":
            return 0
        if state.get("status") == "failed":
            return 1
        time.sleep(max(1.0, interval))


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team Android M2 Auto-E2E Watcher")
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    parser.add_argument("--state", default=".orchestrator/android-e2e/state.json")
    parser.add_argument("--workspace", default=".android-e2e-smoke")
    parser.add_argument("--adb", default=os.getenv("ADB_BIN") or shutil.which("adb") or "adb")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    state_path = Path(args.state).expanduser().resolve()
    if args.reset and state_path.exists():
        state_path.unlink()
    return watch(
        bridge_url=args.bridge,
        state_path=state_path,
        workspace=Path(args.workspace).expanduser().resolve(),
        adb_bin=args.adb,
        interval=args.interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
