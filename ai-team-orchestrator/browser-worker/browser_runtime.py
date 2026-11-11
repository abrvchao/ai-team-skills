#!/usr/bin/env python3
"""Thin Browser Worker runtime backed by the locally installed Google Chrome.

输入来自 PullWorker 生成的 request.json。当前 V0.1 只允许只读导航和证据采集，
不执行登录、表单提交、购买、发布等外部副作用动作。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _safe_url(task: dict) -> str:
    """从结构化 metadata 或 prompt 提取 URL，并只允许 http/https。"""
    metadata = dict(task.get("metadata") or {})
    url = str(metadata.get("url") or "").strip()
    if not url:
        match = URL_RE.search(str(task.get("prompt") or ""))
        url = match.group(0).rstrip(".,);]}") if match else ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Browser Worker requires one valid http/https URL")
    return url


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _run(argv: list[str], *, timeout: int = 10) -> tuple[int, str, str, bool]:
    """有界运行 Chrome；macOS headless 偶尔产出证据后仍不退出，因此主动收尾进程组。"""
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return int(process.returncode or 0), stdout or "", stderr or "", False
    except subprocess.TimeoutExpired as exc:
        # 中文说明：只终止本任务独立创建的 Chrome 进程组，不影响用户正在使用的 Chrome。
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout2, stderr2 = process.communicate(timeout=2)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                pass
            stdout2, stderr2 = process.communicate()
        return 124, _text(exc.stdout) + _text(stdout2), _text(exc.stderr) + _text(stderr2), True


def execute(request_file: Path, artifact_manifest: Path) -> int:
    task = json.loads(request_file.read_text(encoding="utf-8"))
    url = _safe_url(task)
    workspace = Path(task["workspace"]).resolve()
    task_dir = request_file.parent
    chrome = os.getenv("CHROME_BIN", DEFAULT_CHROME)
    if not Path(chrome).is_file():
        raise FileNotFoundError(f"Chrome executable not found: {chrome}")

    dom_file = task_dir / "page.html"
    screenshot_file = task_dir / "page.png"
    report_file = task_dir / "browser_report.json"

    # 中文说明：每个任务使用独立临时 profile，避免读取个人浏览器 cookie、登录态或扩展。
    profile_dir = task_dir / "chrome-profile"
    common = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile_dir}",
    ]

    dom_code, dom_stdout, dom_stderr, dom_forced_stop = _run(common + [
        "--virtual-time-budget=5000", "--run-all-compositor-stages-before-draw", "--dump-dom", url
    ])
    dom_file.write_text(dom_stdout, encoding="utf-8")
    shot_code, shot_stdout, shot_stderr, shot_forced_stop = _run(common + [
        "--virtual-time-budget=5000", "--run-all-compositor-stages-before-draw",
        f"--screenshot={screenshot_file}", "--window-size=1440,1000", url
    ])

    title_match = re.search(r"<title[^>]*>(.*?)</title>", dom_stdout, re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    report = {
        "url": url,
        "title": title,
        "dom_exit_code": dom_code,
        "screenshot_exit_code": shot_code,
        "dom_forced_stop_after_capture": dom_forced_stop,
        "screenshot_forced_stop_after_capture": shot_forced_stop,
        "dom_bytes": dom_file.stat().st_size if dom_file.exists() else 0,
        "screenshot_bytes": screenshot_file.stat().st_size if screenshot_file.exists() else 0,
        "runtime": "google-chrome-headless",
        "mode": "read_only",
    }
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 中文说明：通过 PullWorker 的 artifact manifest 上报成果，Bridge 仍负责真实任务状态。
    artifacts = [
        {"path": str(report_file), "kind": "file", "label": "browser report"},
        {"path": str(dom_file), "kind": "file", "label": "rendered DOM"},
    ]
    if screenshot_file.is_file() and screenshot_file.stat().st_size > 0:
        artifacts.append({"path": str(screenshot_file), "kind": "image", "label": "browser screenshot"})
    artifact_manifest.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in artifacts),
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False))
    # 中文说明：macOS Chrome 被有界收尾（exit 124）不是失败；只有证据没产出才失败。
    if not dom_file.stat().st_size:
        print(dom_stderr[-2000:])
        return 2
    if not screenshot_file.is_file() or screenshot_file.stat().st_size == 0:
        print(shot_stderr[-2000:])
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team Browser Worker Chrome runtime")
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--artifact-manifest", required=True)
    args = parser.parse_args()
    return execute(Path(args.request_file), Path(args.artifact_manifest))


if __name__ == "__main__":
    raise SystemExit(main())
