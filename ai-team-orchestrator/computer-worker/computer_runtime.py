#!/usr/bin/env python3
"""macOS read-only Computer Worker runtime（本机电脑只读执行器）."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ALLOWED_APPS = {"Finder", "TextEdit", "Safari", "Google Chrome"}


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_target(task: dict) -> Path:
    """只允许 reveal task workspace 内的路径，避免读取任意本机文件。"""
    workspace = Path(task["workspace"]).resolve()
    metadata = dict(task.get("metadata") or {})
    raw = str(metadata.get("path") or ".").strip()
    target = Path(raw)
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    if not _inside(target, workspace):
        raise ValueError("Computer Worker target must stay inside task workspace")
    if not target.exists():
        raise FileNotFoundError(target)
    return target


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def execute(request_file: Path, artifact_manifest: Path) -> int:
    task = json.loads(request_file.read_text(encoding="utf-8"))
    metadata = dict(task.get("metadata") or {})
    action = str(metadata.get("action") or "reveal_path").strip()
    task_dir = request_file.parent
    screenshot = task_dir / "desktop.png"
    report_file = task_dir / "computer_report.json"

    report: dict[str, object] = {"action": action, "mode": "read_only", "runtime": "macos-gui"}
    if action == "reveal_path":
        target = _resolve_target(task)
        # 中文说明：open -R 只在 Finder 中显示目标，不修改文件内容。
        result = _run(["/usr/bin/open", "-R", str(target)])
        if result.returncode != 0:
            print(result.stderr)
            return 2
        _run(["/usr/bin/osascript", "-e", 'tell application "Finder" to activate'])
        report["target"] = str(target)
    elif action == "activate_app":
        app = str(metadata.get("app") or "").strip()
        if app not in ALLOWED_APPS:
            raise ValueError(f"Computer Worker app is not allowed: {app}")
        result = _run(["/usr/bin/open", "-a", app])
        if result.returncode != 0:
            print(result.stderr)
            return 3
        report["app"] = app
    else:
        raise ValueError(f"Unsupported read-only computer action: {action}")

    time.sleep(1)
    # 中文说明：Finder front window（前台窗口目录）比 selection（选中项）更稳定，
    # 能证明 GUI 已真实导航到目标所在目录，同时不依赖 Screen Recording 权限。
    if action == "reveal_path":
        target_path = Path(str(report["target"])).resolve()
        front_dir = ""
        for _ in range(10):
            state = _run([
                "/usr/bin/osascript",
                "-e",
                'tell application "Finder" to if (count of windows) > 0 then return POSIX path of (target of front window as alias)',
            ])
            front_dir = state.stdout.strip()
            if front_dir:
                try:
                    if Path(front_dir).resolve() == target_path.parent:
                        break
                except Exception:
                    pass
            time.sleep(0.3)
        report["finder_front_directory"] = front_dir
        report["front_directory_matches_target_parent"] = bool(
            front_dir and Path(front_dir).resolve() == target_path.parent
        )

        # selection 作为补充证据：有就记录，没有不影响主证据。
        selection = _run([
            "/usr/bin/osascript",
            "-e",
            'tell application "Finder" to if (count of selection) > 0 then return POSIX path of (item 1 of selection as alias)',
        ])
        report["selected_path"] = selection.stdout.strip()
        if not report["front_directory_matches_target_parent"]:
            print(state.stderr or f"Finder front directory mismatch: {front_dir}")
            return 4

    # 中文说明：后台服务可能没有屏幕录制权限；截图是附加证据，不覆盖已验证的 GUI 状态真相。
    shot = _run(["/usr/sbin/screencapture", "-x", str(screenshot)])
    screenshot_ok = shot.returncode == 0 and screenshot.is_file() and screenshot.stat().st_size > 0
    report["screenshot_available"] = screenshot_ok
    report["screenshot_bytes"] = screenshot.stat().st_size if screenshot_ok else 0
    if not screenshot_ok:
        report["screenshot_note"] = (shot.stderr or shot.stdout or "Screen Recording permission unavailable").strip()

    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = [json.dumps({"path": str(report_file), "kind": "file", "label": "computer report"}, ensure_ascii=False)]
    if screenshot_ok:
        artifacts.append(json.dumps({"path": str(screenshot), "kind": "image", "label": "desktop screenshot"}, ensure_ascii=False))
    artifact_manifest.write_text("\n".join(artifacts) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team macOS Computer Worker runtime")
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--artifact-manifest", required=True)
    args = parser.parse_args()
    return execute(Path(args.request_file), Path(args.artifact_manifest))


if __name__ == "__main__":
    raise SystemExit(main())
