#!/usr/bin/env python3
"""AndroidDeviceWorker read-only runtime（安卓设备只读执行器）."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def parse_devices(output: str) -> list[dict[str, str]]:
    """解析 adb devices -l，只接受状态为 device 的已授权设备。"""
    rows: list[dict[str, str]] = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        # 中文说明：ADB 版本对 `devices -l` 的列分隔并不稳定；新版本可能用空格对齐而不是制表符。
        # 这里只依赖“序列号 + 状态”这两个空白分隔字段，避免把真实已授权设备误判为不存在。
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        meta: dict[str, str] = {"serial": serial, "state": state}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                meta[key] = value
        if state == "device":
            rows.append(meta)
    return rows


def _adb_bin() -> str:
    value = os.getenv("ADB_BIN") or shutil.which("adb")
    if not value:
        raise FileNotFoundError("adb executable is not available")
    return value


def _run_text(argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def _select_device(adb: str, requested: str = "") -> dict[str, str]:
    result = _run_text([adb, "devices", "-l"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "adb devices failed")
    devices = parse_devices(result.stdout)
    if requested:
        for item in devices:
            if item["serial"] == requested:
                return item
        raise RuntimeError(f"requested Android device is not connected/authorized: {requested}")
    if not devices:
        raise RuntimeError("no authorized Android device connected")
    if len(devices) > 1:
        raise RuntimeError("multiple Android devices connected; metadata.serial is required")
    return devices[0]


def _prop(adb: str, serial: str, name: str) -> str:
    result = _run_text([adb, "-s", serial, "shell", "getprop", name])
    return result.stdout.strip() if result.returncode == 0 else ""


def execute(request_file: Path, artifact_manifest: Path) -> int:
    task = json.loads(request_file.read_text(encoding="utf-8"))
    metadata = dict(task.get("metadata") or {})
    action = str(metadata.get("action") or "inspect_device")
    if action != "inspect_device":
        raise ValueError(f"Unsupported read-only Android action: {action}")

    adb = _adb_bin()
    device = _select_device(adb, str(metadata.get("serial") or "").strip())
    serial = device["serial"]
    task_dir = request_file.parent
    report_file = task_dir / "android_report.json"
    screenshot_file = task_dir / "android_screen.png"

    report = {
        "action": action,
        "mode": "read_only",
        "runtime": "adb",
        "serial": serial,
        "model": _prop(adb, serial, "ro.product.model"),
        "manufacturer": _prop(adb, serial, "ro.product.manufacturer"),
        "android_version": _prop(adb, serial, "ro.build.version.release"),
        "sdk": _prop(adb, serial, "ro.build.version.sdk"),
        "device_metadata": device,
    }

    # 中文说明：exec-out screencap 只读取当前屏幕，不点击、不输入、不修改应用状态。
    shot = subprocess.run(
        [adb, "-s", serial, "exec-out", "screencap", "-p"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    screenshot_ok = shot.returncode == 0 and len(shot.stdout) > 100
    if screenshot_ok:
        screenshot_file.write_bytes(shot.stdout)
    report["screenshot_available"] = screenshot_ok
    report["screenshot_bytes"] = len(shot.stdout) if screenshot_ok else 0
    if not screenshot_ok:
        report["screenshot_error"] = shot.stderr.decode("utf-8", errors="replace")[-1000:]

    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = [json.dumps({"path": str(report_file), "kind": "file", "label": "android device report"}, ensure_ascii=False)]
    if screenshot_ok:
        artifacts.append(json.dumps({"path": str(screenshot_file), "kind": "image", "label": "android screenshot"}, ensure_ascii=False))
    artifact_manifest.write_text("\n".join(artifacts) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team AndroidDeviceWorker runtime")
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--artifact-manifest", required=True)
    args = parser.parse_args()
    return execute(Path(args.request_file), Path(args.artifact_manifest))


if __name__ == "__main__":
    raise SystemExit(main())
