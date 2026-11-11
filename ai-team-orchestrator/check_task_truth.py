"""Validate task-manifest truth against durable repository artifacts."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Callable, Iterable


COMPLETED_PREFIXES = ("completed", "done")
NOT_STARTED_STATUSES = {"queued_not_started", "blocked_not_configured"}


def declared_artifacts(task: dict) -> list[str]:
    paths: list[str] = []
    result = task.get("result")
    if isinstance(result, dict):
        artifact = result.get("artifact")
        if isinstance(artifact, str) and artifact.strip():
            paths.append(artifact.strip())
    for item in task.get("outputs") or []:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or " " in value:
            continue
        suffix = Path(value).suffix.casefold()
        if suffix in {".md", ".json", ".jsonl", ".csv", ".txt", ".yaml", ".yml"}:
            paths.append(value)
    seen: set[str] = set()
    return [path for path in paths if not (path in seen or seen.add(path))]


def _git_tracked_checker(repo_root: Path) -> Callable[[str], bool]:
    def is_tracked(relative_path: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", relative_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    return is_tracked


def validate_manifest(
    manifest: dict,
    *,
    repo_root: Path,
    is_tracked: Callable[[str], bool] | None = None,
) -> list[str]:
    tracked = is_tracked or _git_tracked_checker(repo_root)
    errors: list[str] = []

    for task in manifest.get("tasks") or []:
        task_id = str(task.get("task_id") or "<unknown>")
        status = str(task.get("status") or "")
        artifacts = declared_artifacts(task)
        present = [path for path in artifacts if (repo_root / path).is_file()]
        durable = [path for path in present if tracked(path)]

        if status.startswith(COMPLETED_PREFIXES):
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            artifact = str(result.get("artifact") or "").strip()
            if not artifact:
                errors.append(f"{task_id}: completed task is missing result.artifact")
                continue
            if not (repo_root / artifact).is_file():
                errors.append(f"{task_id}: completed artifact does not exist: {artifact}")
            elif not tracked(artifact):
                errors.append(f"{task_id}: completed artifact is not tracked by git: {artifact}")

        if status in NOT_STARTED_STATUSES and durable:
            errors.append(
                f"{task_id}: status={status} but tracked artifact already exists: "
                + ", ".join(durable)
            )

    return errors


def iter_manifests(tasks_dir: Path) -> Iterable[Path]:
    yield from sorted(tasks_dir.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AI Team task manifests against git artifacts")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parent.parent),
    )
    parser.add_argument(
        "--tasks-dir",
        default=str(Path(__file__).resolve().parent / "tasks"),
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    tasks_dir = Path(args.tasks_dir).expanduser().resolve()
    failures: list[str] = []
    for path in iter_manifests(tasks_dir):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{path.name}: invalid JSON: {exc}")
            continue
        for error in validate_manifest(manifest, repo_root=repo_root):
            failures.append(f"{path.name}: {error}")

    if failures:
        for failure in failures:
            print(f"ERROR {failure}")
        return 1
    print(f"task truth OK ({len(list(iter_manifests(tasks_dir)))} manifest(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
