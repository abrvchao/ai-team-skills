#!/usr/bin/env python3
"""Daily Delivery Brief (每日交付简报): local, read-only CLI.

Aggregates AI Team truth sources into one concise, human-readable execution
brief:

* an AI Team run root (``progress.json`` + ``delivery_plan.json``);
* the optional orchestrator run JSON (``--run-json``), which records the
  artifacts each role actually produced;
* the optional program status file (``--status-file``, default auto-discovered
  ``docs/AI_TEAM_STATUS.md`` walking up from the run root) for the current
  execution priority.

Truth rules enforced here (Chinese: 真实性规则):
* a missing or unreadable source is rendered as ``EVIDENCE GAP: <source>``,
  never as a plausible-looking placeholder;
* only files that actually exist on disk are reported as delivered artifacts;
* the CLI never writes to a source file -- the sole write is an explicit
  ``--output`` target chosen by the caller;
* no database, cache, network, or long-running service is used (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

RUN_STATE_FILE = "progress.json"
DELIVERY_PLAN_FILE = "delivery_plan.json"
RUNS_DIR = ".ai-team-runs"
STATUS_RELATIVE = Path("docs") / "AI_TEAM_STATUS.md"
PRIORITY_HEADING = "Current execution priority"

# Artifact locations a run root can contain when no orchestrator run JSON is
# supplied. These are read-only glob patterns, not assumed-to-exist files.
ARTIFACT_PATTERNS = (
    "primary/*/primary.md",
    "primary/*/*/primary.md",
    "reviews/*/review.md",
    "synthesis/final.md",
    "implementation/implementation_report.md",
)


def evidence_gap(source: str, detail: Optional[str] = None) -> str:
    """Render an explicit evidence-gap marker for a missing truth source."""
    marker = f"EVIDENCE GAP: {source}"
    return f"{marker} ({detail})" if detail else marker


def safe_load_json(path: Path) -> tuple[Optional[Any], Optional[str]]:
    """Load JSON read-only; return ``(data, error_message)`` without raising."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "file not found"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:  # unreadable/permission -- degrade, do not crash
        return None, f"unreadable: {exc}"


def discover_status_file(start: Path) -> Optional[Path]:
    """Walk up from ``start`` looking for ``docs/AI_TEAM_STATUS.md``.

    Relative discovery keeps the tool portable; no machine-specific absolute
    path is ever baked into the code.
    """
    for base in (start, *start.parents):
        candidate = base / STATUS_RELATIVE
        if candidate.is_file():
            return candidate
    return None


def resolve_run_root(base: Path) -> Path:
    """Resolve the run root from a run directory or a workspace directory.

    A run directory contains ``progress.json``; a workspace directory contains
    ``.ai-team-runs/<run_id>/``. When several runs exist, the most recently
    updated one is chosen so a bare default invocation still works.
    """
    base = base.resolve()
    if (base / RUN_STATE_FILE).is_file() or (base / DELIVERY_PLAN_FILE).is_file():
        return base
    runs_dir = base / RUNS_DIR
    if runs_dir.is_dir():
        candidates = [
            child
            for child in runs_dir.iterdir()
            if child.is_dir() and (child / RUN_STATE_FILE).is_file()
        ]
        if candidates:
            return max(
                candidates,
                key=lambda child: (child / RUN_STATE_FILE).stat().st_mtime,
            )
    raise FileNotFoundError(
        f"no {RUN_STATE_FILE} or {DELIVERY_PLAN_FILE} at {base} "
        f"and no run under {runs_dir}"
    )


def parse_priority(status_text: str) -> Optional[str]:
    """Extract the first item of the 'Current execution priority' list."""
    in_section = False
    for raw_line in status_text.splitlines():
        line = raw_line.strip()
        if line.startswith("#") and PRIORITY_HEADING in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("#"):
                break
            if line.startswith("1."):
                return line[2:].strip() or None
    return None


def display_path(path: Path, run_root: Optional[Path]) -> str:
    """Prefer a path relative to the run root for readability/portability."""
    if run_root is not None:
        try:
            return str(path.resolve().relative_to(run_root.resolve()))
        except ValueError:
            pass
    return str(path)


def format_participants(
    plan: dict[str, Any], progress: dict[str, Any]
) -> Optional[str]:
    participants = plan.get("participants")
    if isinstance(participants, list) and participants:
        rendered = []
        for participant in participants:
            if isinstance(participant, dict):
                worker = participant.get("worker_id")
                label = participant.get("role_label") or participant.get("role_id")
                if worker and label and label != worker:
                    rendered.append(f"{worker} ({label})")
                elif worker or label:
                    rendered.append(str(worker or label))
            elif participant:
                rendered.append(str(participant))
        if rendered:
            return ", ".join(rendered)

    health = progress.get("worker_health")
    if isinstance(health, dict) and health:
        assigned = [
            worker
            for worker, entry in health.items()
            if isinstance(entry, dict)
            and (entry.get("active_task_count") or entry.get("roles_attempted"))
        ]
        if assigned:
            return f"{', '.join(assigned)} (from progress.worker_health)"
    return None


def collect_artifacts(
    run_root: Optional[Path], run_json: Optional[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Return ``(delivered, gaps)`` -- only existing files count as delivered."""
    delivered: list[str] = []
    gaps: list[str] = []

    if isinstance(run_json, dict):
        recorded: list[tuple[str, Any]] = []
        for worker, entry in (run_json.get("primary") or {}).items():
            if isinstance(entry, dict) and entry.get("artifact"):
                recorded.append((f"primary/{worker}", entry["artifact"]))
        for name, entry in (run_json.get("reviews") or {}).items():
            if isinstance(entry, dict) and entry.get("artifact"):
                recorded.append((f"reviews/{name}", entry["artifact"]))
        synthesis = run_json.get("synthesis")
        if isinstance(synthesis, dict) and synthesis.get("artifact"):
            recorded.append(("synthesis", synthesis["artifact"]))
        implementation = run_json.get("implementation")
        if isinstance(implementation, dict):
            artifact = implementation.get("artifact") or implementation.get(
                "artifact_relative"
            )
            if artifact:
                recorded.append(("implementation", artifact))
        if run_json.get("final_artifact"):
            recorded.append(("final", run_json["final_artifact"]))

        if recorded:
            seen_paths: set[str] = set()
            for label, raw in recorded:
                path = Path(raw)
                candidates = [path]
                if not path.is_absolute() and run_root is not None:
                    # artifact_relative is workspace-relative while most other
                    # fields are run-root-relative; try both before declaring a gap.
                    candidates = [
                        run_root / path,
                        run_root.parent / path,
                        run_root.parent.parent / path,
                    ]
                resolved = next((c for c in candidates if c.is_file()), candidates[0])
                shown = display_path(resolved, run_root)
                key = str(resolved.resolve()) if resolved.exists() else shown
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                if resolved.is_file():
                    delivered.append(f"{shown} ({label})")
                else:
                    gaps.append(
                        evidence_gap(
                            f"{label} artifact",
                            f"recorded but missing: {shown}",
                        )
                    )
            return delivered, gaps

    # No orchestrator run JSON (or it recorded nothing): read the run root.
    if run_root is not None and run_root.is_dir():
        for pattern in ARTIFACT_PATTERNS:
            for path in sorted(run_root.glob(pattern)):
                if path.is_file():
                    delivered.append(display_path(path, run_root))
    return delivered, gaps


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def render_brief(
    *,
    run_root: Optional[Path],
    progress: Optional[dict[str, Any]],
    plan: Optional[dict[str, Any]],
    run_json: Optional[dict[str, Any]],
    status_text: Optional[str],
    status_source: str,
    progress_source: str,
    plan_source: str,
    run_json_source: Optional[str],
    generated_at: str,
) -> str:
    progress = progress if isinstance(progress, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    run_json = run_json if isinstance(run_json, dict) else {}

    gaps: list[str] = []

    run_id = _first_text(
        progress.get("run_id"), run_json.get("run_id"),
        run_root.name if run_root else None,
    )
    if run_id is None:
        run_id = evidence_gap("run_id")
        gaps.append(run_id)

    if status_text is None:
        priority = evidence_gap(status_source, "status file missing or unreadable")
        gaps.append(priority)
    else:
        parsed = parse_priority(status_text)
        if parsed is None:
            priority = evidence_gap(
                f"{status_source}#{PRIORITY_HEADING}",
                "priority section not found",
            )
            gaps.append(priority)
        else:
            priority = parsed

    goal = _first_text(plan.get("goal"), run_json.get("goal"))
    if goal is None:
        goal = evidence_gap(f"{plan_source}#goal")
        gaps.append(goal)

    phase = _first_text(progress.get("current_phase"))
    if phase is None:
        phase = evidence_gap(f"{progress_source}#current_phase")
        gaps.append(phase)
    run_status = _first_text(progress.get("run_status"), run_json.get("status"))
    if run_status is None:
        run_status = evidence_gap(f"{progress_source}#run_status")
        gaps.append(run_status)

    schedule = progress.get("schedule")
    schedule = schedule if isinstance(schedule, dict) else {}
    schedule_state = _first_text(schedule.get("state"))
    if schedule_state is None:
        schedule_state = evidence_gap(f"{progress_source}#schedule.state")
        gaps.append(schedule_state)

    due = _first_text(plan.get("project_due_at"), schedule.get("project_due_at"))
    if due is None:
        due = evidence_gap(
            f"{plan_source}#project_due_at / {progress_source}#schedule.project_due_at"
        )
        gaps.append(due)

    workers = format_participants(plan, progress)
    if workers is None:
        workers = evidence_gap(
            f"{plan_source}#participants / {progress_source}#worker_health"
        )
        gaps.append(workers)

    if "blockers" in progress:
        blockers_value = progress.get("blockers")
        if isinstance(blockers_value, list):
            if blockers_value:
                blockers = "; ".join(str(item) for item in blockers_value)
            else:
                blockers = "None recorded (blockers=[])"
        elif blockers_value:
            blockers = str(blockers_value)
        else:
            blockers = "None recorded (blockers empty)"
    else:
        blockers = evidence_gap(f"{progress_source}#blockers")
        gaps.append(blockers)

    next_action = _first_text(progress.get("next_action"))
    if next_action is None:
        next_action = evidence_gap(f"{progress_source}#next_action")
        gaps.append(next_action)

    delivered, artifact_gaps = collect_artifacts(run_root, run_json or None)
    gaps.extend(artifact_gaps)

    lines = [
        f"# Daily Delivery Brief — {run_id}",
        "",
        f"**Current Priority**: {priority}",
        f"**Goal**: {goal}",
        f"**Phase / Status**: {phase} / {run_status}",
        f"**Schedule State**: {schedule_state}",
        f"**Project Due At**: {due}",
        f"**Planned / Assigned Workers**: {workers}",
        f"**Blockers**: {blockers}",
        f"**Next Action**: {next_action}",
    ]
    if delivered:
        lines.append("**Delivered Artifacts**:")
        lines.extend(f"- {artifact}" for artifact in delivered)
    else:
        missing = evidence_gap("delivered artifacts", "no artifact files found")
        lines.append(f"**Delivered Artifacts**: {missing}")
        gaps.append(missing)

    lines.extend(["", "## Evidence Gaps"])
    if gaps:
        # 去重但保留首次出现顺序，避免同一缺口在多处重复。
        seen: set[str] = set()
        for gap in gaps:
            if gap not in seen:
                seen.add(gap)
                lines.append(f"- {gap}")
    else:
        lines.append("- None recorded.")

    sources = [f"read: {progress_source}"]
    if plan:
        sources.append(f"read: {plan_source}")
    if run_json:
        sources.append(f"read: {run_json_source}")
    if status_text is not None:
        sources.append(f"read: {status_source}")
    else:
        sources.append(f"missing: {status_source}")

    lines.extend(
        [
            "",
            "---",
            f"Generated at: {generated_at}",
            f"Sources: {'; '.join(sources)}",
            "Read-only CLI. No database or long-running service.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_brief(
    *,
    run_root: Optional[Path],
    run_json_path: Optional[Path],
    status_file: Optional[Path],
    generated_at: str,
) -> str:
    progress: Optional[dict[str, Any]] = None
    plan: Optional[dict[str, Any]] = None
    run_json: Optional[dict[str, Any]] = None
    progress_error: Optional[str] = None
    plan_error: Optional[str] = None
    run_json_error: Optional[str] = None

    progress_source = RUN_STATE_FILE
    plan_source = DELIVERY_PLAN_FILE
    run_json_source: Optional[str] = None

    if run_root is not None:
        progress_source = display_path(run_root / RUN_STATE_FILE, run_root)
        plan_source = display_path(run_root / DELIVERY_PLAN_FILE, run_root)
        progress, progress_error = safe_load_json(run_root / RUN_STATE_FILE)
        plan, plan_error = safe_load_json(run_root / DELIVERY_PLAN_FILE)
        progress = progress if isinstance(progress, dict) else None
        plan = plan if isinstance(plan, dict) else None

    if run_json_path is not None:
        run_json_source = str(run_json_path)
        run_json, run_json_error = safe_load_json(run_json_path)
        run_json = run_json if isinstance(run_json, dict) else None

    status_text: Optional[str] = None
    resolved_status = status_file
    if resolved_status is None and run_root is not None:
        resolved_status = discover_status_file(run_root)
    status_source = str(resolved_status) if resolved_status else str(STATUS_RELATIVE)
    if resolved_status is not None:
        try:
            status_text = resolved_status.read_text(encoding="utf-8")
        except OSError:
            status_text = None

    brief = render_brief(
        run_root=run_root,
        progress=progress,
        plan=plan,
        run_json=run_json,
        status_text=status_text,
        status_source=status_source,
        progress_source=progress_source,
        plan_source=plan_source,
        run_json_source=run_json_source,
        generated_at=generated_at,
    )

    # 逐源错误只作为证据缺口提示，绝不伪装成正常数据。
    errors = {
        "progress.json": progress_error,
        "delivery_plan.json": plan_error,
        "run_json": run_json_error,
    }
    notes = [
        f"EVIDENCE GAP: {name} ({error})"
        for name, error in errors.items()
        if error and error != "file not found"
    ]
    if notes:
        brief = brief.rstrip("\n") + "\n\n## Source Errors\n" + "\n".join(
            f"- {note}" for note in notes
        ) + "\n"
    return brief


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Daily Delivery Brief from local AI Team truth sources. "
            "Read-only; no database or service."
        )
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("."),
        help="Run directory (contains progress.json) or workspace dir (contains .ai-team-runs/).",
    )
    parser.add_argument(
        "--run-json",
        type=Path,
        default=None,
        help="Optional orchestrator run JSON for authoritative artifact records.",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help=f"Optional program status file (default: auto-discover {STATUS_RELATIVE}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the brief to. Source truth files are never modified.",
    )
    parser.add_argument(
        "--generated-at",
        default=None,
        help="Override the generated-at timestamp (ISO 8601); useful for tests.",
    )
    args = parser.parse_args(argv)

    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()

    try:
        run_root = resolve_run_root(args.run_root)
    except FileNotFoundError as exc:
        if args.run_json is None:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        run_root = None

    if args.run_json is not None and not args.run_json.is_file():
        print(f"ERROR: run JSON not found: {args.run_json}", file=sys.stderr)
        return 2

    brief = generate_brief(
        run_root=run_root,
        run_json_path=args.run_json,
        status_file=args.status_file,
        generated_at=generated_at,
    )

    if args.output:
        try:
            args.output.write_text(brief, encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: cannot write output {args.output}: {exc}", file=sys.stderr)
            return 2
        print(f"Brief written to {args.output}")
    else:
        print(brief, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
