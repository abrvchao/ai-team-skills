"""Build deterministic AI Team release bundle（构建确定性的 AI Team 发布包）."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "ai-team-orchestrator"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wanted_files() -> list[Path]:
    """Return explicit release allowlist（返回显式发布白名单）, avoiding secrets/runtime state."""
    files: set[Path] = set()
    top_names = {
        "README.md", "LOCAL_INSTALL.md", "requirements-mcp.txt", "requirements-mcp.lock.txt",
        "bridge.py", "dispatcher.py", "registry.py", "store.py", "supervisor.py", "team_supervisor.py",
        "mcp_server.py", "product_intake.py", "project_dashboard.py", "release_report.py", "build_release_bundle.py",
        "android_device_worker.py", "android_e2e_watcher.py", "browser_worker.py", "computer_worker.py",
    }
    for name in top_names:
        path = ORCH / name
        if path.is_file():
            files.add(path)

    for base in [ORCH / "scripts", ORCH / "schemas"]:
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    files.add(path)

    # 中文说明：Node Worker 仅打包源码、测试和 package 元数据，不打包 node_modules、日志、凭据或运行态。
    for worker in [ORCH / "node-worker", *sorted(ORCH.glob("*-node-worker"))]:
        if not worker.is_dir():
            continue
        for name in ("package.json", "package-lock.json", "README.md"):
            path = worker / name
            if path.is_file():
                files.add(path)
        for folder in ("src", "test", "tests"):
            base = worker / folder
            if base.is_dir():
                for path in base.rglob("*"):
                    if path.is_file() and path.suffix in {".js", ".mjs", ".cjs", ".json"}:
                        files.add(path)

    # 中文说明：同时带上 V1 控制面文档，方便离线理解当前架构、计划和安装方式。
    docs = ROOT / "docs"
    if docs.is_dir():
        for path in docs.glob("AI_TEAM*.md"):
            if path.is_file():
                files.add(path)
        for name in ("ROADMAP.md", "BACKLOG.md", "PROGRESS_PROTOCOL.md"):
            path = docs / name
            if path.is_file():
                files.add(path)

    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def build(output: Path) -> dict:
    entries = []
    content: dict[str, bytes] = {}
    for path in _wanted_files():
        rel = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        content[rel] = data
        entries.append({"path": rel, "size": len(data), "sha256": _sha256(data)})

    manifest = {
        "format": "ai-team-release-v0.1",
        "file_count": len(entries),
        "files": entries,
        "excludes": ["secrets", "API keys", ".venv", "node_modules", ".orchestrator", ".ai-team-runs", "logs"],
    }
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    sums_data = "".join(f"{row['sha256']}  {row['path']}\n" for row in entries).encode()

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in sorted(content):
            info = zipfile.ZipInfo(rel, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, content[rel], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        for rel, data in (("RELEASE_MANIFEST.json", manifest_data), ("SHA256SUMS.txt", sums_data)):
            info = zipfile.ZipInfo(rel, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    blob = output.read_bytes()
    return {"path": str(output), "sha256": _sha256(blob), "bytes": len(blob), "file_count": len(entries)}


def verify(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle, "r") as zf:
        manifest = json.loads(zf.read("RELEASE_MANIFEST.json"))
        failures = []
        for row in manifest["files"]:
            try:
                data = zf.read(row["path"])
            except KeyError:
                failures.append(f"missing:{row['path']}")
                continue
            if len(data) != row["size"] or _sha256(data) != row["sha256"]:
                failures.append(f"mismatch:{row['path']}")
    return {"ok": not failures, "failures": failures, "file_count": manifest["file_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team deterministic release bundle")
    parser.add_argument("--output", default="/tmp/ai-team-v1.zip")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    path = Path(args.output).expanduser().resolve()
    if args.verify:
        result = verify(path)
    else:
        result = build(path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
