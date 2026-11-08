"""Automatic multi-agent supervisor for AI Team Orchestrator.

Truth remains in the Bridge. This layer only plans task graphs, stages artifacts,
and advances phases after bridge-reported terminal states.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from delivery_plan import build_delivery_plan, schedule_state
from supervisor import BridgeClient
from team_handoff import (
    WorkerRosterMonitor, prepare_handoff, save_handoff_state, update_handoffs,
)


TEAM_ORDER = ["dsh", "gemini", "grok", "qwen", "muse", "doubao", "mimo", "workbuddy", "opencode"]
# 中文说明：MiMo 是研究/审查 Worker，没有代码执行权限；不能派实施和可执行验证。
SYNTHESIS_PREFERENCE = ["gemini", "muse", "qwen", "workbuddy", "doubao", "dsh", "mimo", "opencode", "grok"]
IMPLEMENTATION_PREFERENCE = ["dsh", "workbuddy", "qwen", "gemini", "muse", "doubao", "grok"]
VALIDATION_PREFERENCE = ["qwen", "dsh", "workbuddy", "gemini", "grok", "doubao", "muse"]
TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE_RUN_STATUSES = {
    "primary_running", "review_running", "synthesis_running",
    "implementation_running", "validation_running", "repair_running",
}
IMPLEMENTATION_REPORT = "implementation_report.md"
VALIDATION_REPORT = "validation_report.md"
REPAIR_REPORT = "repair_report.md"
ARTIFACT_IN_PROGRESS_MARKER = "AI_TEAM_ARTIFACT_STATUS: in_progress"
PRODUCT_TASK_CONTRACT_VERSION = "product-task-v0.2"
REVIEW_CONTRACT_VERSION = "review-v0.2"
EXECUTION_BUDGET_VERSION = "execution-budget-v0.1"
REVIEW_REQUIRED_MARKERS = [
    "## ACCEPTED_CHECKS",
    "## FAILED_CHECKS",
    "## COUNTER_EVIDENCE",
    "## UNSUPPORTED_CLAIMS",
    "## REQUIRED_REPAIR",
    "VERDICT:",
]
VALIDATION_VERDICT_RE = re.compile(
    r"^[*_\`\s]*VERDICT\s*:\s*(accept|pass|repair|blocked)[*_\`\s]*$",
    re.IGNORECASE,
)

ROLE_PROFILES: dict[str, dict[str, Any]] = {
    "dsh": {
        "role_id": "engineering_verifier",
        "fallbacks": ["qwen", "gemini", "muse"],
        "label": "Engineering Verifier",
        "brief": (
            "Focus on implementation feasibility, local/runtime validation, interfaces, "
            "tests, failure modes, and concrete engineering steps. Prefer evidence from "
            "the workspace or runnable checks over speculation."
        ),
    },
    "gemini": {
        "role_id": "deep_researcher",
        "fallbacks": ["qwen", "doubao", "grok"],
        "label": "Deep Researcher",
        "brief": (
            "Focus on deep research, long-context synthesis, system-level dependencies, "
            "evidence gaps, and a coherent explanatory model. Separate facts from assumptions."
        ),
    },
    "grok": {
        "role_id": "trend_scout_red_team",
        "fallbacks": ["gemini", "qwen", "doubao"],
        "label": "Trend Scout & Red Team",
        "brief": (
            "Focus on current trends, external reality checks, adversarial analysis, weak "
            "assumptions, missing counter-evidence, and what could invalidate the plan."
        ),
    },
    "qwen": {
        "role_id": "implementation_architect",
        "fallbacks": ["dsh", "gemini", "muse"],
        "label": "Implementation Architect",
        "brief": (
            "Focus on structured solution design, implementation alternatives, decomposition, "
            "interfaces, acceptance criteria, and efficient execution paths."
        ),
    },
    "muse": {
        "role_id": "long_context_engineer_reviewer",
        "fallbacks": ["gemini", "qwen", "dsh"],
        "label": "Long-Context Engineer & Reviewer",
        "brief": (
            "Focus on long-horizon coding work, large-context repository reasoning, multimodal "
            "engineering evidence, implementation quality, and independent code/design review."
        ),
    },
    "doubao": {
        "role_id": "china_context_specialist",
        "fallbacks": ["qwen", "gemini", "grok"],
        "label": "China Context Specialist",
        "brief": (
            "Focus on Chinese-language context, domestic platforms/ecosystems, localization, "
            "operational constraints, and whether conclusions survive China-market reality."
        ),
    },
    "mimo": {
        "role_id": "independent_research_reviewer",
        "fallbacks": ["qwen", "dsh", "workbuddy"],
        "label": "Independent Research Reviewer",
        "brief": (
            "Review facts, assumptions, risks, and alternative explanations using only "
            "the evidence explicitly supplied in the prompt. This Worker cannot browse "
            "repository files or run shell commands. Never claim executable tests passed."
        ),
    },
    "workbuddy": {
        "role_id": "implementation_quality_engineer",
        "fallbacks": ["dsh", "qwen", "mimo"],
        "label": "Implementation Quality Engineer",
        "brief": (
            "Review implementability, test coverage, interfaces and failure handling. "
            "Use the bounded staged workspace and report only observed checks. "
            "Do not modify shared production files during research/review phases."
        ),
    },
    "opencode": {
        "role_id": "free_readonly_code_reviewer",
        "fallbacks": ["workbuddy", "mimo", "qwen"],
        "label": "Free Read-only Code Reviewer",
        "brief": (
            "Independent read-only research and code review via OpenCode/MiMo. "
            "Use only supplied evidence and bounded workspace context; never claim "
            "actual code execution or tests. This role cannot implement source changes."
        ),
    },
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _run_id(goal: str) -> str:
    seed = f"{goal}|{time.time_ns()}|{os.getpid()}".encode("utf-8")
    return "run_" + hashlib.sha256(seed).hexdigest()[:20]


class TeamRunStore:
    def __init__(self, root: str | Path = ".orchestrator/team-runs") -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, run: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            run["updated_at"] = _utcnow()
            _atomic_json(self.path(run["run_id"]), run)
            return run

    def load(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            path = self.path(run_id)
            if not path.exists():
                raise KeyError(f"unknown team run: {run_id}")
            return json.loads(path.read_text(encoding="utf-8"))

    def list(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted(self.root.glob("run_*.json")):
                try:
                    run = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if active_only and run.get("status") not in ACTIVE_RUN_STATUSES:
                    continue
                items.append(run)
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return items


class TeamSupervisor:
    def __init__(
        self,
        bridge: BridgeClient,
        *,
        state_dir: str | Path = ".orchestrator/team-runs",
    ) -> None:
        self.bridge = bridge
        self.runs = TeamRunStore(state_dir)
        self.roster_monitor = WorkerRosterMonitor(state_dir)

    def worker_health_snapshot(
        self,
        run: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """汇总全局可用性与当前 run 内的真实健康状态。"""
        by_id = {item["agent_id"]: item for item in self.bridge.agents()}
        health: dict[str, dict[str, Any]] = {}
        for worker_id in TEAM_ORDER:
            global_state = by_id.get(worker_id, {})
            configured = bool(global_state.get("configured"))
            available = bool(global_state.get("available"))
            queueable = bool(global_state.get("queueable"))
            globally_eligible = configured and available and queueable
            health[worker_id] = {
                "worker_id": worker_id,
                "global_status": (
                    "available"
                    if globally_eligible
                    else ("unavailable" if configured else "not_configured")
                ),
                "configured": configured,
                "available": available,
                "queueable": queueable,
                "run_local_health": "healthy" if globally_eligible else "unavailable",
                "eligible_for_new_assignment": globally_eligible,
                "attempt_count": 0,
                "active_task_count": 0,
                "completed_artifact_count": 0,
                "roles_attempted": [],
                "roles_completed": [],
                "failure_classes": [],
                "reasons": [],
            }

        if not run:
            return health

        def record(
            worker_id: str | None,
            *,
            phase: str,
            role: str,
            status: str | None,
            failure_class: str | None,
            artifact_ready: bool,
        ) -> None:
            if not worker_id or worker_id not in health:
                return
            row = health[worker_id]
            row["attempt_count"] += 1
            if role not in row["roles_attempted"]:
                row["roles_attempted"].append(role)
            if status not in TERMINAL:
                row["active_task_count"] += 1
            if artifact_ready:
                row["completed_artifact_count"] += 1
                if role not in row["roles_completed"]:
                    row["roles_completed"].append(role)

            # 失败但已有完整成果：成果可继续使用，但本轮不再给该 Worker 增加新任务。
            if status in {"failed", "cancelled"} and artifact_ready:
                if row["run_local_health"] == "healthy":
                    row["run_local_health"] = "degraded"
                reason = f"{phase}:process_failed_but_artifact_ready"
                if reason not in row["reasons"]:
                    row["reasons"].append(reason)

            # 失败且没有完整成果，或“完成”却缺少必需成果，都视为本轮不健康。
            if (
                status in {"failed", "cancelled"} and not artifact_ready
            ) or (
                status == "completed" and not artifact_ready
            ):
                row["run_local_health"] = "unhealthy"
                reason = f"{phase}:terminal_without_ready_artifact"
                if reason not in row["reasons"]:
                    row["reasons"].append(reason)
            if failure_class and failure_class not in row["failure_classes"]:
                row["failure_classes"].append(failure_class)

        for role_owner, entry in (run.get("primary") or {}).items():
            role_id = str(
                entry.get("role_id")
                or ROLE_PROFILES.get(role_owner, {}).get("role_id")
                or role_owner
            )
            attempts = list(entry.get("attempts") or [])
            if not attempts:
                attempts = [{
                    "worker_id": entry.get("worker_id") or role_owner,
                    "workspace": entry.get("workspace"),
                    "status": entry.get("status"),
                    "failure_class": entry.get("failure_class"),
                }]
            for attempt in attempts:
                workspace = attempt.get("workspace")
                artifact_ready = bool(
                    workspace
                    and self._artifact_ready_path(Path(workspace) / "primary.md")
                )
                record(
                    str(attempt.get("worker_id") or role_owner),
                    phase="primary",
                    role=role_id,
                    status=attempt.get("status"),
                    failure_class=attempt.get("failure_class"),
                    artifact_ready=artifact_ready,
                )

        for review_id, entry in (run.get("reviews") or {}).items():
            workspace = entry.get("workspace")
            record(
                entry.get("reviewer"),
                phase="review",
                role=(
                    "review:"
                    + str(entry.get("target_role") or entry.get("target") or review_id)
                ),
                status=entry.get("status"),
                failure_class=entry.get("failure_class"),
                artifact_ready=bool(
                    workspace
                    and self._artifact_ready_path(Path(workspace) / "review.md")
                ),
            )

        synthesis = run.get("synthesis") or {}
        if synthesis:
            workspace = synthesis.get("workspace")
            record(
                synthesis.get("agent_id"),
                phase="synthesis",
                role="synthesis",
                status=synthesis.get("status"),
                failure_class=synthesis.get("failure_class"),
                artifact_ready=bool(
                    workspace
                    and self._artifact_ready_path(Path(workspace) / "final.md")
                ),
            )

        implementation = run.get("implementation") or {}
        if implementation:
            workspace = implementation.get("workspace")
            relative = implementation.get("artifact_relative") or IMPLEMENTATION_REPORT
            record(
                implementation.get("agent_id"),
                phase="implementation",
                role="implementation",
                status=implementation.get("status"),
                failure_class=implementation.get("failure_class"),
                artifact_ready=bool(
                    workspace
                    and self._artifact_ready_path(Path(workspace) / relative)
                ),
            )

        # Repair（修复）也属于真实产品执行，必须进入本轮健康历史。
        repair_entries = list(run.get("repairs") or [])
        current_repair = run.get("repair") or {}
        if current_repair:
            repair_entries.append(current_repair)
        for repair in repair_entries:
            workspace = repair.get("workspace")
            relative = repair.get("artifact_relative") or REPAIR_REPORT
            record(
                repair.get("agent_id"),
                phase="repair",
                role="repair",
                status=repair.get("status"),
                failure_class=repair.get("failure_class"),
                artifact_ready=bool(
                    workspace
                    and self._artifact_ready_path(Path(workspace) / relative)
                ),
            )

        # 历史 Validation（验证）也要保留，否则失败过的 Validator 可能被后续循环重新选中。
        validation_history = list(run.get("validation_history") or [])
        validation = run.get("validation") or {}
        if validation:
            validation_history.append(validation)
        for validation in validation_history:
            attempts = list(validation.get("attempts") or [])
            if not attempts:
                attempts = [{
                    "worker_id": validation.get("agent_id"),
                    "status": validation.get("status"),
                    "failure_class": validation.get("failure_class"),
                    "artifact_relative": validation.get("artifact_relative"),
                }]
            for attempt in attempts:
                relative = (
                    attempt.get("artifact_relative")
                    or validation.get("artifact_relative")
                    or VALIDATION_REPORT
                )
                workspace = validation.get("workspace") or run.get("workspace")
                record(
                    attempt.get("worker_id"),
                    phase="validation",
                    role="validation",
                    status=attempt.get("status"),
                    failure_class=attempt.get("failure_class"),
                    artifact_ready=bool(
                        workspace
                        and self._artifact_ready_path(Path(workspace) / relative)
                    ),
                )

        for row in health.values():
            # 全局不可用优先级最高；degraded/unhealthy 也不再接收新的本轮任务。
            if row["global_status"] != "available":
                row["run_local_health"] = "unavailable"
                row["eligible_for_new_assignment"] = False
            else:
                row["eligible_for_new_assignment"] = (
                    row["run_local_health"] == "healthy"
                )
        return health

    def _eligible_workers(self, run: dict[str, Any] | None = None) -> list[str]:
        snapshot = self.worker_health_snapshot(run)
        if run is not None:
            run["worker_health"] = snapshot
        return [
            worker_id
            for worker_id in TEAM_ORDER
            if snapshot.get(worker_id, {}).get("eligible_for_new_assignment")
        ]

    def available_agents(self) -> list[str]:
        # 初始分工尚未产生 run 历史，因此复用同一健康模型的全局层。
        return self._eligible_workers(None)

    def _execution_budget(self, run: dict[str, Any], phase: str) -> dict[str, Any]:
        """为每个阶段生成与 Supervisor 截止时间一致的探索/收尾双预算合同。"""
        policy = run.get("policy") or {}
        soft_seconds = int(policy.get(f"{phase}_soft_timeout_seconds", 90))
        hard_seconds = int(policy.get(f"{phase}_hard_timeout_seconds", 300))

        # Worker 本地探索超时必须略晚于 Supervisor 软截止，给消息传递和主动中断留出缓冲；
        # 不能再出现“合同说可探索 360 秒，但 Supervisor 90 秒就强制收尾”的双重真相。
        handoff_grace_seconds = min(30, max(10, soft_seconds // 3))
        exploration_seconds = min(
            max(30, soft_seconds + handoff_grace_seconds),
            max(30, hard_seconds - 60),
        )

        # 收尾预算独立保留，并保证探索 + 收尾不会超过 Supervisor 硬截止。
        remaining_after_exploration = max(30, hard_seconds - exploration_seconds)
        finalization_seconds = min(120, remaining_after_exploration)
        exploration_tool_calls = {
            "primary": 28,
            "review": 16,
            # 中文说明：综合阶段需要读取多个已完成成果并收敛验收边界。
            # 仍保持有界，但避免在真实收敛任务中因一步之差误杀整个 Run。
            "synthesis": 20,
            "implementation": 40,
            "validation": 32,
            "repair": 40,
        }.get(phase, 20)
        return {
            "version": EXECUTION_BUDGET_VERSION,
            "phase": phase,
            "supervisor_soft_deadline_seconds": soft_seconds,
            "supervisor_hard_deadline_seconds": hard_seconds,
            "handoff_grace_seconds": handoff_grace_seconds,
            "exploration": {
                "timeout_seconds": exploration_seconds,
                "max_tool_calls": exploration_tool_calls,
            },
            "finalization": {
                "timeout_seconds": finalization_seconds,
                # 0 表示支持零工具模式的 Worker 在收尾阶段不得再调用工具。
                "max_tool_calls": 0,
                "allow_new_exploration": False,
            },
        }

    @staticmethod
    def _entry_execution_percent(entry: dict[str, Any] | None, artifact_ready: bool = False) -> int:
        if not entry:
            return 0
        status = str(entry.get("status") or "queued")
        if status in TERMINAL:
            return 100
        if status == "running":
            return 85 if artifact_ready else 50
        return 0

    def progress_snapshot(self, run: dict[str, Any]) -> dict[str, Any]:
        """Return delivery progress without conflating execution with acceptance/coverage."""
        implementation_enabled = bool(run.get("policy", {}).get("implementation_enabled"))
        phases = ["primary", "review", "synthesis"]
        if implementation_enabled:
            phases.append("implementation")
            # 只有真正进入过修复循环时才显示 Repair（修复）阶段，避免普通成功 run 被虚增一个阶段。
            if run.get("repair") or run.get("repairs"):
                phases.append("repair")
            phases.append("validation")

        current_phase = str(run.get("phase") or "primary")
        if current_phase in phases:
            current_index = phases.index(current_phase)
        elif run.get("status") == "completed":
            current_index = len(phases)
        else:
            reached = "primary"
            if run.get("reviews"):
                reached = "review"
            if run.get("synthesis"):
                reached = "synthesis"
            if implementation_enabled and run.get("implementation"):
                reached = "implementation"
            if implementation_enabled and (run.get("repair") or run.get("repairs")):
                reached = "repair"
            if implementation_enabled and run.get("validation"):
                reached = "validation"
            current_index = phases.index(reached)
        phase_rows: dict[str, dict[str, Any]] = {}

        for index, phase in enumerate(phases):
            if phase == "primary":
                entries = list((run.get("primary") or {}).values())
                percents = [
                    self._entry_execution_percent(entry, self._artifact_ready(entry, "primary.md"))
                    for entry in entries
                ]
                task_count = len(entries)
                terminal_count = sum(1 for entry in entries if entry.get("status") in TERMINAL)
            elif phase == "review":
                entries = list((run.get("reviews") or {}).values())
                percents = [
                    self._entry_execution_percent(entry, self._artifact_ready(entry, "review.md"))
                    for entry in entries
                ]
                task_count = len(entries)
                terminal_count = sum(1 for entry in entries if entry.get("status") in TERMINAL)
            else:
                entry = run.get(phase)
                filename = {
                    "synthesis": "final.md",
                    "implementation": str((entry or {}).get("artifact_relative") or IMPLEMENTATION_REPORT),
                    "repair": str((entry or {}).get("artifact_relative") or REPAIR_REPORT),
                    "validation": str((entry or {}).get("artifact_relative") or VALIDATION_REPORT),
                }[phase]
                artifact_ready = False
                if entry:
                    if phase == "synthesis":
                        artifact_ready = self._artifact_ready(entry, filename)
                    else:
                        path = Path(run.get("workspace") or ".") / filename
                        artifact_ready = self._artifact_ready_path(path)
                entries = [entry] if entry else []
                percents = [self._entry_execution_percent(entry, artifact_ready)] if entry else []
                task_count = len(entries)
                terminal_count = sum(1 for item in entries if item and item.get("status") in TERMINAL)

            if percents:
                percent = int(round(sum(percents) / len(percents)))
            elif index < current_index or run.get("status") == "completed":
                percent = 100
            else:
                percent = 0

            if percent >= 100:
                state = "complete"
            elif phase == current_phase and run.get("status") in ACTIVE_RUN_STATUSES:
                state = "in_progress"
            elif run.get("status") in {"failed", "needs_repair", "blocked"} and index > current_index:
                state = "blocked"
            elif index < current_index:
                state = "complete"
            else:
                state = "pending"

            phase_rows[phase] = {
                "state": state,
                "percent": percent,
                "task_count": task_count,
                "terminal_count": terminal_count,
            }

        overall = int(round(sum(row["percent"] for row in phase_rows.values()) / max(1, len(phase_rows))))
        next_actions = {
            "primary_running": "finish primary role artifacts or bounded reassignment",
            "review_running": "finish independent structured reviews",
            "synthesis_running": "converge evidence, disagreements and coverage into final synthesis",
            "implementation_running": "apply/verify accepted decision in the real product workspace",
            "validation_running": "independently re-run acceptance evidence and issue a verdict",
            "repair_running": "repair only the failed acceptance checks, then revalidate independently",
            "needs_repair": "create bounded repair task, then independently revalidate",
            "blocked": "resolve the named blocker or change the product decision before resuming",
            "completed": "record product progress and select the next gated backlog item",
            "failed": "resolve the named blocker before resuming the product plan",
        }
        blockers: list[str] = []
        blockers.extend(str(item) for item in (run.get("errors") or []))
        coverage = run.get("coverage_gaps") or {}
        if coverage:
            blockers.append("coverage_gaps=" + json.dumps(coverage, ensure_ascii=False, sort_keys=True))

        health = self.worker_health_snapshot(run)
        return {
            "version": "run-progress-v0.1",
            "overall_percent": overall,
            "current_phase": current_phase,
            "run_status": run.get("status"),
            "phases": phase_rows,
            "coverage_gaps": coverage,
            "worker_health": health,
            "blockers": blockers,
            "next_action": next_actions.get(str(run.get("status")), "inspect current run state"),
            "schedule": {
                "state": schedule_state(
                    run.get("delivery_plan") or {},
                    completed=run.get("status") == "completed",
                ),
                "project_due_at": (run.get("delivery_plan") or {}).get("project_due_at"),
            },
            "updated_at": _utcnow(),
        }

    def _attach_progress(self, run: dict[str, Any]) -> dict[str, Any]:
        update_handoffs(
            run,
            artifact_ready=lambda entry, phase: (
                self._artifact_ready(entry, "primary.md")
                if phase == "primary"
                else self._artifact_ready_path(
                    Path(entry.get("workspace") or run.get("workspace") or ".")
                    / str(entry.get("artifact_relative") or VALIDATION_REPORT)
                )
            ),
        )
        run["worker_health"] = self.worker_health_snapshot(run)
        run["progress"] = self.progress_snapshot(run)
        handoffs = run.get("handoffs") or []
        run["progress"]["handoffs"] = {
            "total": len(handoffs),
            "pending": sum(h.get("status") in {"assigned", "prepared"} for h in handoffs),
            "in_progress": sum(h.get("status") == "in_progress" for h in handoffs),
            "delivered": sum(h.get("status") == "delivered" for h in handoffs),
            "failed": sum(h.get("status") == "failed" for h in handoffs),
            "latest": [
                {k: h.get(k) for k in ("handoff_id", "phase", "from_worker",
                                      "to_worker", "status", "source_task_id",
                                      "new_task_id", "manifest")}
                for h in handoffs[-8:]
            ],
        }
        progress_path = Path(run["run_root"]) / "progress.json"
        _atomic_json(progress_path, run["progress"])
        return run

    def _select_participants(
        self,
        requested: list[str] | None,
        *,
        allow_single_worker_degraded: bool = False,
    ) -> list[str]:
        available = self.available_agents()
        if requested:
            unknown = [item for item in requested if item not in TEAM_ORDER]
            if unknown:
                raise ValueError(f"unsupported team agents: {', '.join(unknown)}")
            selected = [item for item in TEAM_ORDER if item in requested and item in available]
        else:
            selected = available
        minimum = 1 if allow_single_worker_degraded else 2
        if len(selected) < minimum:
            raise RuntimeError(
                f"team run requires at least {minimum} available worker"
                f"{'s' if minimum != 1 else ''}; "
                f"available={available!r}, selected={selected!r}"
            )
        return selected

    def _capture_workspace_baseline(self, workspace: Path, run_root: Path) -> dict[str, Any]:
        """在任何 run 产物写入工作区前，记录可审计的运行前基线。"""
        baseline_dir = run_root / "baseline"
        meta: dict[str, Any] = {
            "version": "workspace-baseline-v0.1",
            "captured_at": _utcnow(),
            "workspace": str(workspace),
            "git": {"is_repo": False},
        }

        # 先读取 Git 状态，再创建 .ai-team-runs 目录；否则基线会把自己的产物算进 dirty worktree。
        probe = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        status_text = ""
        tracked_diff = ""
        untracked_manifest: list[dict[str, Any]] = []
        if probe.returncode == 0:
            repo_root = Path(probe.stdout.strip()).resolve()
            head = subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            )
            status = subprocess.run(
                ["git", "-C", str(workspace), "status", "--porcelain=v1", "--untracked-files=all", "--", "."],
                capture_output=True,
                text=True,
            )
            diff = subprocess.run(
                ["git", "-C", str(workspace), "diff", "--binary", "HEAD", "--", "."],
                capture_output=True,
                text=True,
            )
            untracked = subprocess.run(
                ["git", "-C", str(workspace), "ls-files", "--others", "--exclude-standard", "-z", "--", "."],
                capture_output=True,
            )
            # 旧 run 目录不是产品基线的一部分；过滤它们避免进度/证据自我污染。
            status_lines = [
                line for line in status.stdout.splitlines()
                if ".ai-team-runs/" not in line
            ]
            status_text = "\n".join(status_lines) + ("\n" if status_lines else "")
            tracked_diff = diff.stdout if diff.returncode == 0 else ""

            if untracked.returncode == 0:
                for raw in untracked.stdout.split(b"\0"):
                    if not raw:
                        continue
                    relative = raw.decode("utf-8", errors="surrogateescape")
                    if ".ai-team-runs/" in relative or relative.startswith(".ai-team-runs/"):
                        continue
                    path = repo_root / relative
                    if not path.is_file():
                        continue
                    digest = hashlib.sha256()
                    try:
                        with path.open("rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                digest.update(chunk)
                        size = path.stat().st_size
                    except OSError:
                        continue
                    untracked_manifest.append({
                        "path": relative,
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                    })

            try:
                workspace_relative = str(workspace.relative_to(repo_root)) or "."
            except ValueError:
                workspace_relative = str(workspace)
            meta["git"] = {
                "is_repo": True,
                "repo_root": str(repo_root),
                "workspace_relative": workspace_relative,
                "head_commit": head.stdout.strip() if head.returncode == 0 else None,
                "dirty": bool(status_text.strip()),
            }

        baseline_dir.mkdir(parents=True, exist_ok=True)
        status_path = baseline_dir / "status.txt"
        diff_path = baseline_dir / "tracked.patch"
        untracked_path = baseline_dir / "untracked.json"
        status_path.write_text(status_text, encoding="utf-8")
        diff_path.write_text(tracked_diff, encoding="utf-8")
        _atomic_json(untracked_path, untracked_manifest)
        meta["artifacts"] = {
            "status": str(status_path),
            "tracked_patch": str(diff_path),
            "untracked_manifest": str(untracked_path),
        }
        meta["untracked_count"] = len(untracked_manifest)
        meta_path = baseline_dir / "baseline.json"
        meta["metadata_file"] = str(meta_path)
        _atomic_json(meta_path, meta)
        return meta

    def _primary_prompt(
        self,
        goal: str,
        role_owner: str,
        worker_id: str,
        product_contract: dict[str, Any],
        *,
        context_excerpt: str = "",
    ) -> str:
        profile = ROLE_PROFILES[role_owner]
        acceptance = "\n".join(f"- {item}" for item in product_contract.get("acceptance") or []) or "- Produce a decision-quality role artifact."
        evidence = "\n".join(f"- {item}" for item in product_contract.get("evidence_required") or []) or "- Distinguish evidence from assumptions."
        stop_conditions = "\n".join(f"- {item}" for item in product_contract.get("stop_conditions") or []) or "- Stop once the mandatory artifact is complete and checked."
        context_note = (
            "Required context artifacts are staged under inputs/context/. "
            "Read them before analysis and treat them as authoritative supplied context."
            if product_contract.get("context_artifacts")
            else "No required context artifacts were supplied."
        )
        return f"""You are executing the product role {profile['label']} in a multi-agent team.

Stable role id: {profile['role_id']}
Current worker: {worker_id}
Preferred worker for this role: {role_owner}

Shared goal:
{goal}

Your independent responsibility:
{profile['brief']}

Product task contract: {product_contract.get('version')}
Acceptance checks:
{acceptance}

Evidence required:
{evidence}

Stop conditions:
{stop_conditions}

Fallback policy: {product_contract.get('fallback_policy')}

Context artifacts:
{context_note}

Supplied context excerpt（文件工具失效时也必须使用的上下文摘要）:
--- BEGIN SUPPLIED CONTEXT ---
{context_excerpt or '[no inline context excerpt]'}
--- END SUPPLIED CONTEXT ---

Deliverable rules:
- Work independently; do not assume other agents agree with you.
- Be concise but evidence-oriented.
- Do not browse/search externally unless the shared goal materially requires current external evidence.
- Once the mandatory artifact is complete and validated, finish the task instead of extending exploration.
- Explicitly list: findings, evidence/assumptions, risks, and recommendations.
- Create `primary.md` in the workspace. That file is mandatory.
- Do not edit anything outside this isolated workspace.
"""

    @staticmethod
    def _context_artifact_excerpt(
        destination: Path,
        staged_files: list[str],
        *,
        max_chars: int = 24000,
    ) -> str:
        """把已暂存 context 做成有界内嵌摘要，避免 Worker 文件工具失败导致上下文消失。"""
        if not staged_files:
            return ""
        lines: list[str] = []
        remaining = max_chars
        for relative in staged_files:
            path = destination / relative
            try:
                payload = path.read_text(encoding="utf-8")
            except Exception as exc:
                payload = f"[CONTEXT READ ERROR: {exc}]"
            excerpt = payload[:remaining]
            lines.extend([
                f"--- BEGIN {relative} ---",
                excerpt,
                f"--- END {relative} ---",
            ])
            remaining -= len(excerpt)
            if remaining <= 0:
                lines.append("[CONTEXT TRUNCATED: full files remain under inputs/context/]")
                break
        return "\n".join(lines)

    def _stage_context_artifacts(
        self,
        *,
        base_workspace: Path,
        product_contract: dict[str, Any],
        destination: Path,
    ) -> list[str]:
        """把合同声明的只读上下文复制到隔离 Worker workspace，避免 Worker 自己猜证据位置。"""
        declared = list(product_contract.get("context_artifacts") or [])
        if not declared:
            return []
        root = base_workspace.resolve()
        target_root = destination / "inputs" / "context"
        target_root.mkdir(parents=True, exist_ok=True)
        staged: list[str] = []
        for index, raw in enumerate(declared, start=1):
            source = Path(str(raw)).expanduser()
            source = source.resolve() if source.is_absolute() else (root / source).resolve()
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"context artifact escapes workspace: {raw}") from exc
            if not source.is_file():
                raise FileNotFoundError(f"context artifact missing: {source}")
            target = target_root / f"{index:03d}-{source.name}"
            shutil.copy2(source, target)
            staged.append(str(target.relative_to(destination)))
        return staged

    @staticmethod
    def _execution_policy(
        selected: list[str],
        *,
        implementation_enabled: bool,
    ) -> dict[str, Any]:
        """生成统一执行预算，保证 preview（预览）与真实 run 使用同一套日程口径。"""
        return {
            "primary_soft_timeout_seconds": 90,
            "primary_hard_timeout_seconds": 420,
            "review_soft_timeout_seconds": 90,
            "review_hard_timeout_seconds": 300,
            "synthesis_soft_timeout_seconds": 90,
            "synthesis_hard_timeout_seconds": 300,
            "unavailable_reassign_grace_seconds": 20,
            "min_primary_success": min(3, len(selected)),
            "min_review_success": min(2, len(selected)),
            "allow_degraded_synthesis": True,
            "implementation_enabled": bool(implementation_enabled),
            # 中文说明：实现/修复阶段需要真实读写代码和跑窄测试，因此使用更长预算。
            "implementation_soft_timeout_seconds": 180,
            "implementation_hard_timeout_seconds": 420,
            "validation_soft_timeout_seconds": 120,
            "validation_hard_timeout_seconds": 300,
            "repair_soft_timeout_seconds": 180,
            "repair_hard_timeout_seconds": 420,
            "max_repair_attempts": 2,
        }

    def plan_run(
        self,
        *,
        goal: str,
        participants: list[str] | None = None,
        fallback_policy: str = "reassign|salvage|degrade",
        implementation_enabled: bool = True,
    ) -> dict[str, Any]:
        """零 Worker 成本生成真实可执行 Delivery Plan（交付计划）预览。"""
        if not goal.strip():
            raise ValueError("team goal is required")
        fallback_modes = {
            item.strip().casefold()
            for item in str(fallback_policy or "").split("|")
            if item.strip()
        }
        allow_single_worker_degraded = (
            "degrade" in fallback_modes and "reassign" not in fallback_modes
        )
        selected = self._select_participants(
            participants,
            allow_single_worker_degraded=allow_single_worker_degraded,
        )
        policy = self._execution_policy(
            selected,
            implementation_enabled=implementation_enabled,
        )
        created_at = _utcnow()
        delivery_plan = build_delivery_plan(
            goal=goal,
            participants=selected,
            role_profiles=ROLE_PROFILES,
            policy=policy,
            created_at=created_at,
            implementation_enabled=implementation_enabled,
        )
        return {
            "version": "goal-plan-preview-v0.1",
            "goal": goal.strip(),
            "created_at": created_at,
            "participants": selected,
            "roles": [
                {
                    "worker_id": worker_id,
                    "role_id": ROLE_PROFILES[worker_id]["role_id"],
                    "role_label": ROLE_PROFILES[worker_id]["label"],
                }
                for worker_id in selected
            ],
            "fallback_policy": fallback_policy,
            "implementation_enabled": bool(implementation_enabled),
            "policy": policy,
            "delivery_plan": delivery_plan,
            "worker_tasks_started": 0,
        }

    def start_run(
        self,
        *,
        goal: str,
        workspace: str = ".",
        participants: list[str] | None = None,
        acceptance: list[str] | None = None,
        evidence_required: list[str] | None = None,
        stop_conditions: list[str] | None = None,
        fallback_policy: str = "reassign|salvage|degrade",
        implementation_enabled: bool = False,
        context_artifacts: list[str] | None = None,
        deliverable_artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        if not goal.strip():
            raise ValueError("team goal is required")
        preview = self.plan_run(
            goal=goal,
            participants=participants,
            fallback_policy=fallback_policy,
            implementation_enabled=implementation_enabled,
        )
        selected = list(preview["participants"])
        run_id = _run_id(goal)
        base_workspace = Path(workspace).expanduser().resolve()
        base_workspace.mkdir(parents=True, exist_ok=True)
        normalized_deliverables: list[str] = []
        for value in deliverable_artifacts or []:
            path = Path(str(value))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("deliverable artifacts must be workspace-relative")
            normalized = path.as_posix()
            if normalized and normalized not in normalized_deliverables:
                normalized_deliverables.append(normalized)
        run_root = base_workspace / ".ai-team-runs" / run_id
        workspace_baseline = self._capture_workspace_baseline(base_workspace, run_root)
        product_contract = {
            "version": PRODUCT_TASK_CONTRACT_VERSION,
            "objective": goal.strip(),
            "acceptance": list(acceptance or ["Produce the mandatory artifact and support material claims with evidence."]),
            "evidence_required": list(evidence_required or ["Separate observed evidence, assumptions, and unresolved gaps."]),
            "stop_conditions": list(stop_conditions or ["Stop new exploration once acceptance can be evaluated and the mandatory artifact is complete."]),
            "fallback_policy": fallback_policy,
            "context_artifacts": list(context_artifacts or []),
            "deliverable_artifacts": normalized_deliverables,
        }
        policy = dict(preview["policy"])
        created_at = str(preview["created_at"])
        run: dict[str, Any] = {
            "version": "1.0",
            "run_id": run_id,
            "goal": goal,
            "workspace": str(base_workspace),
            "run_root": str(run_root),
            "status": "primary_running",
            "phase": "primary",
            "participants": selected,
            "product_contract": product_contract,
            "workspace_baseline": workspace_baseline,
            "roles": {
                ROLE_PROFILES[worker_id]["role_id"]: {
                    "role_id": ROLE_PROFILES[worker_id]["role_id"],
                    "label": ROLE_PROFILES[worker_id]["label"],
                    "preferred_worker": worker_id,
                    "current_worker": worker_id,
                    "fallbacks": list(ROLE_PROFILES[worker_id].get("fallbacks", [])),
                }
                for worker_id in selected
            },
            "created_at": created_at,
            "updated_at": _utcnow(),
            "phase_started_at": _utcnow(),
            "policy": policy,
            "nudges": {},
            "primary": {},
            "reviews": {},
            "synthesis": None,
            "implementation": None,
            "repair": None,
            "repairs": [],
            "validation": None,
            "validation_history": [],
            "errors": [],
        }
        if len(selected) == 1:
            run["degraded"] = True
            run.setdefault("warnings", []).append(
                "single-worker degraded start: only one healthy Worker is available; "
                "independent review/validation remains mandatory before final acceptance"
            )
        run["delivery_plan"] = dict(preview["delivery_plan"])
        _atomic_json(run_root / "delivery_plan.json", run["delivery_plan"])
        for agent_id in selected:
            agent_workspace = run_root / "primary" / agent_id
            agent_workspace.mkdir(parents=True, exist_ok=True)
            staged_context = self._stage_context_artifacts(
                base_workspace=base_workspace,
                product_contract=product_contract,
                destination=agent_workspace,
            )
            context_excerpt = self._context_artifact_excerpt(agent_workspace, staged_context)
            task = self.bridge.submit(
                agent_id=agent_id,
                title=f"[{run_id}] Primary: {ROLE_PROFILES[agent_id]['label']}",
                prompt=self._primary_prompt(
                    goal,
                    agent_id,
                    agent_id,
                    product_contract,
                    context_excerpt=context_excerpt,
                ),
                workspace=str(agent_workspace),
                metadata={
                    "team_run_id": run_id,
                    "team_phase": "primary",
                    "team_role": ROLE_PROFILES[agent_id]["role_id"],
                    "team_role_label": ROLE_PROFILES[agent_id]["label"],
                    "preferred_worker": agent_id,
                    "current_worker": agent_id,
                    "execution_budget": self._execution_budget(run, "primary"),
                    "task_contract": {**product_contract, "role_id": ROLE_PROFILES[agent_id]["role_id"], "role_label": ROLE_PROFILES[agent_id]["label"]},
                    "expected_artifacts": ["primary.md"],
                    "context_artifacts": staged_context,
                    "context_excerpt": context_excerpt,
                },
            )
            run["primary"][agent_id] = {
                "role_id": ROLE_PROFILES[agent_id]["role_id"],
                "role_label": ROLE_PROFILES[agent_id]["label"],
                "preferred_worker": agent_id,
                "original_worker": agent_id,
                "worker_id": agent_id,
                "task_id": task["task_id"],
                "workspace": str(agent_workspace),
                "status": task["status"],
                "started": bool(task.get("started")),
                "artifact": None,
                "reassignment_count": 0,
                "attempts": [
                    {
                        "worker_id": agent_id,
                        "task_id": task["task_id"],
                        "workspace": str(agent_workspace),
                        "status": task["status"],
                        "failure_class": None,
                    }
                ],
            }
        self._attach_progress(run)
        self.runs.save(run)
        return run

    def get_run(self, run_id: str, *, refresh: bool = True) -> dict[str, Any]:
        run = self.runs.load(run_id)
        if refresh and run.get("status") in ACTIVE_RUN_STATUSES:
            run = self._refresh_task_views(run)
        self._attach_progress(run)
        self.runs.save(run)
        return run

    def _refresh_task_views(self, run: dict[str, Any]) -> dict[str, Any]:
        for entry in run.get("primary", {}).values():
            self._refresh_entry(entry)
        for entry in run.get("reviews", {}).values():
            self._refresh_entry(entry)
        if run.get("synthesis"):
            self._refresh_entry(run["synthesis"])
        if run.get("implementation"):
            self._refresh_entry(run["implementation"])
        if run.get("repair"):
            self._refresh_entry(run["repair"])
        if run.get("validation"):
            self._refresh_entry(run["validation"])
        return run

    def _refresh_entry(self, entry: dict[str, Any]) -> None:
        task = self.bridge.task(entry["task_id"])
        entry["status"] = task["status"]
        entry["started"] = bool(task.get("started"))
        entry["started_at"] = task.get("started_at")
        entry["finished_at"] = task.get("finished_at")
        entry["error"] = task.get("error")
        if task.get("status") in {"failed", "cancelled"}:
            entry["failure_class"] = self._classify_failure(task.get("error"))
        attempts = entry.get("attempts") or []
        if attempts:
            current = attempts[-1]
            if current.get("task_id") == entry.get("task_id"):
                current["status"] = entry.get("status")
                current["failure_class"] = entry.get("failure_class")
                current["error"] = entry.get("error")
                current["finished_at"] = entry.get("finished_at")

    @staticmethod
    def _all_terminal(entries: list[dict[str, Any]]) -> bool:
        return bool(entries) and all(entry.get("status") in TERMINAL for entry in entries)

    @staticmethod
    def _completed(entries: dict[str, dict[str, Any]]) -> list[str]:
        return [key for key, entry in entries.items() if entry.get("status") == "completed"]

    @staticmethod
    def _classify_failure(error: Any) -> str | None:
        text = str(error or "").casefold()
        if not text:
            return None
        if "quota" in text or "usage limit" in text or "setlimitexceeded" in text:
            return "quota_exhausted"
        if "429" in text or "rate limit" in text or "rate_limited" in text:
            return "rate_limited"
        if "auth" in text or "unauthorized" in text or "invalid api key" in text:
            return "auth_required"
        if "model_unavailable" in text or "model unavailable" in text:
            return "model_unavailable"
        if "timeout" in text or "timed out" in text:
            return "timeout"
        if "artifact" in text and "missing" in text:
            return "artifact_missing"
        return "runtime_failure"

    @staticmethod
    def _artifact_if_present(entry: dict[str, Any], filename: str) -> Path | None:
        path = Path(entry["workspace"]) / filename
        return path if path.is_file() else None

    @staticmethod
    def _artifact_ready_path(path: Path | None) -> bool:
        if not path or not path.is_file():
            return False
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
        except Exception:
            return False
        if not head.strip():
            return False
        # Artifact status is a control header, not a forbidden prose token. A
        # finalized report may truthfully mention the old in_progress marker in
        # its body when describing how it converged. Only an actual standalone
        # status line keeps the artifact checkpointed.
        for line in head.splitlines():
            value = line.strip()
            if not value:
                continue
            if value.casefold() == ARTIFACT_IN_PROGRESS_MARKER.casefold():
                return False
            # Once substantive content starts, later prose mentioning the marker
            # is evidence, not control state.
            if value.startswith("#") or value.startswith("AI_TEAM_ARTIFACT_STATUS:"):
                break
        return True

    def _artifact_ready(self, entry: dict[str, Any], filename: str) -> bool:
        return self._artifact_ready_path(self._artifact_if_present(entry, filename))

    def _attempt_artifacts(self, entry: dict[str, Any], filename: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        attempts = entry.get("attempts") or []
        for index, attempt in enumerate(attempts):
            workspace = attempt.get("workspace")
            if not workspace:
                continue
            path = Path(workspace) / filename
            if not path.is_file():
                continue
            record = {
                "attempt": index + 1,
                "worker_id": attempt.get("worker_id"),
                "task_id": attempt.get("task_id"),
                "path": str(path),
                "status": attempt.get("status"),
                "failure_class": attempt.get("failure_class"),
                "ready": self._artifact_ready_path(path),
            }
            found.append(record)
            seen_paths.add(str(path))

        # Review/synthesis/implementation entries do not have primary-style
        # attempts. Even primary entries may point at a current workspace not yet
        # represented in attempts during recovery. Always include the current
        # workspace artifact as a candidate without duplicating an attempt path.
        workspace = entry.get("workspace")
        if workspace:
            current = Path(workspace) / filename
            if current.is_file() and str(current) not in seen_paths:
                found.append({
                    "attempt": len(attempts) + 1 if attempts else 1,
                    "worker_id": entry.get("worker_id") or entry.get("agent_id") or entry.get("reviewer"),
                    "task_id": entry.get("task_id"),
                    "path": str(current),
                    "status": entry.get("status"),
                    "failure_class": entry.get("failure_class"),
                    "ready": self._artifact_ready_path(current),
                    "current_workspace": True,
                })
        entry["attempt_artifacts"] = found
        return found

    def _salvage_entry(self, entry: dict[str, Any], filename: str) -> bool:
        if entry.get("status") == "completed":
            self._attempt_artifacts(entry, filename)
            return False
        candidates = [item for item in self._attempt_artifacts(entry, filename) if item.get("ready")]
        if not candidates:
            return False
        current_path = self._artifact_if_present(entry, filename)
        selected = next((item for item in reversed(candidates) if current_path and item["path"] == str(current_path)), None)
        if selected is None:
            selected = candidates[-1]
        if entry.get("salvaged") and entry.get("salvaged_artifact") == selected["path"]:
            return False
        entry["salvaged"] = True
        entry["salvaged_artifact"] = selected["path"]
        entry["salvaged_from_attempt"] = selected["attempt"]
        entry["salvaged_from_worker"] = selected["worker_id"]
        entry["artifact"] = selected["path"]
        return True

    def _usable(self, entries: dict[str, dict[str, Any]], filename: str) -> list[str]:
        usable: list[str] = []
        for key, entry in entries.items():
            if entry.get("status") == "completed":
                path = self._resolve_artifact(entry, filename)
                if self._artifact_ready_path(path):
                    usable.append(key)
            elif entry.get("salvaged") and Path(str(entry.get("salvaged_artifact") or "")).is_file():
                usable.append(key)
        return usable


    @staticmethod
    def _retryable_failure(entry: dict[str, Any]) -> bool:
        return entry.get("failure_class") in {
            "quota_exhausted",
            "rate_limited",
            "model_unavailable",
            "timeout",
            "runtime_failure",
        }

    def _fallback_candidates(
        self,
        run: dict[str, Any],
        role_owner: str,
        entry: dict[str, Any],
    ) -> list[str]:
        """返回该角色当前可用且未尝试过的健康 Worker。"""
        eligible = set(self._eligible_workers(run))
        tried = {str(item.get("worker_id")) for item in (entry.get("attempts") or [])}
        return [
            worker_id
            for worker_id in ROLE_PROFILES[role_owner].get("fallbacks", [])
            if worker_id in eligible and worker_id not in tried
        ]

    def _fallback_worker(
        self,
        run: dict[str, Any],
        role_owner: str,
        entry: dict[str, Any],
    ) -> str | None:
        # 单角色兼容路径；批量失败时由 Batch Role Planner（批量角色规划器）统一决策。
        candidates = self._fallback_candidates(run, role_owner, entry)
        if not candidates:
            return None
        load = {worker_id: 0 for worker_id in candidates}
        for other in run.get("primary", {}).values():
            worker_id = other.get("worker_id")
            if worker_id in load and other.get("status") not in TERMINAL:
                load[worker_id] += 1
        order = {worker_id: index for index, worker_id in enumerate(ROLE_PROFILES[role_owner].get("fallbacks", []))}
        return min(candidates, key=lambda worker_id: (load[worker_id], order[worker_id]))

    def _plan_primary_reassignments(
        self,
        run: dict[str, Any],
        role_owners: list[str],
    ) -> dict[str, Any]:
        """对同一轮失败角色做一次联合分配，避免逐个贪心形成热点。"""
        plan_id = f"fallback_plan_{len(run.get('fallback_plans') or []) + 1:02d}"
        candidate_rows: list[dict[str, Any]] = []
        unassigned: list[dict[str, Any]] = []

        for role_owner in role_owners:
            entry = run["primary"][role_owner]
            candidates = self._fallback_candidates(run, role_owner, entry)
            tried = [
                str(item.get("worker_id"))
                for item in (entry.get("attempts") or [])
                if item.get("worker_id")
            ]
            if not candidates:
                unassigned.append({
                    "role_owner": role_owner,
                    "role_id": entry["role_id"],
                    "failed_worker": entry.get("worker_id") or role_owner,
                    "failure_class": entry.get("failure_class"),
                    "tried_workers": tried,
                    "reason": "no_healthy_untried_fallback",
                })
                continue
            candidate_rows.append({
                "role_owner": role_owner,
                "role_id": entry["role_id"],
                "failed_worker": entry.get("worker_id") or role_owner,
                "failure_class": entry.get("failure_class"),
                "tried_workers": tried,
                "candidates": candidates,
            })

        active_load = {worker_id: 0 for worker_id in TEAM_ORDER}
        for other in (run.get("primary") or {}).values():
            worker_id = other.get("worker_id")
            if worker_id in active_load and other.get("status") not in TERMINAL:
                active_load[worker_id] += 1

        # 已经贡献过可用角色的 Worker 仍可继续工作，但会有多样性惩罚，避免所有角色收敛到同一模型。
        contribution_count = {worker_id: 0 for worker_id in TEAM_ORDER}
        for successful_owner in self._usable(run.get("primary") or {}, "primary.md"):
            worker_id = (
                run["primary"][successful_owner].get("worker_id")
                or successful_owner
            )
            if worker_id in contribution_count:
                contribution_count[worker_id] += 1

        assignments: list[dict[str, Any]] = []
        objective: dict[str, Any] | None = None
        if candidate_rows:
            candidate_lists = [row["candidates"] for row in candidate_rows]
            best_combo: tuple[str, ...] | None = None
            best_score: tuple[Any, ...] | None = None

            # 角色数很小（当前最多 6 个），直接枚举组合可得到全局确定性最优解。
            for combo in itertools.product(*candidate_lists):
                batch_load = {worker_id: 0 for worker_id in TEAM_ORDER}
                for worker_id in combo:
                    batch_load[worker_id] += 1
                total_load = {
                    worker_id: active_load[worker_id] + batch_load[worker_id]
                    for worker_id in TEAM_ORDER
                }
                max_load = max(total_load.values(), default=0)
                load_square_sum = sum(value * value for value in total_load.values())
                diversity_penalty = sum(
                    contribution_count[worker_id]
                    for worker_id in combo
                )
                preference_penalty = 0
                for row, worker_id in zip(candidate_rows, combo):
                    preference_penalty += row["candidates"].index(worker_id)
                deterministic_tie_break = tuple(
                    TEAM_ORDER.index(worker_id) for worker_id in combo
                )
                score = (
                    max_load,
                    load_square_sum,
                    preference_penalty,
                    diversity_penalty,
                    deterministic_tie_break,
                )
                if best_score is None or score < best_score:
                    best_score = score
                    best_combo = combo

            assert best_combo is not None and best_score is not None
            objective = {
                "max_worker_load": best_score[0],
                "load_square_sum": best_score[1],
                "preference_penalty": best_score[2],
                "diversity_penalty": best_score[3],
                "tie_break": list(best_score[4]),
            }
            for row, worker_id in zip(candidate_rows, best_combo):
                assignments.append({
                    **row,
                    "assigned_worker": worker_id,
                    "preference_rank": row["candidates"].index(worker_id),
                    "existing_contribution_count": contribution_count[worker_id],
                    "active_load_before_plan": active_load[worker_id],
                    "status": "planned",
                })

        health = self.worker_health_snapshot(run)
        return {
            "plan_id": plan_id,
            "created_at": _utcnow(),
            "status": "planned",
            "roles_considered": [
                run["primary"][role_owner]["role_id"] for role_owner in role_owners
            ],
            "assignments": assignments,
            "unassigned": unassigned,
            "objective": objective,
            "health_snapshot": {
                worker_id: {
                    "global_status": row["global_status"],
                    "run_local_health": row["run_local_health"],
                    "eligible_for_new_assignment": row["eligible_for_new_assignment"],
                }
                for worker_id, row in health.items()
            },
        }

    def _reassign_primary_role(
        self,
        run: dict[str, Any],
        role_owner: str,
        *,
        fallback: str | None = None,
        fallback_plan_id: str | None = None,
    ) -> bool:
        entry = run["primary"][role_owner]
        if entry.get("reassignment_count", 0) >= 1:
            return False
        if self._artifact_ready(entry, "primary.md"):
            return False
        if entry.get("status") not in {"failed", "cancelled"} or not self._retryable_failure(entry):
            return False
        fallback = fallback or self._fallback_worker(run, role_owner, entry)
        if not fallback:
            return False
        # 计划生成后到提交前仍要重新校验，防止健康状态或全局可用性发生变化。
        if fallback not in self._fallback_candidates(run, role_owner, entry):
            return False

        attempt_number = int(entry.get("reassignment_count", 0)) + 1
        workspace = Path(run["run_root"]) / "primary" / role_owner / f"fallback-{attempt_number}-{fallback}"
        workspace.mkdir(parents=True, exist_ok=True)
        staged_context = self._stage_context_artifacts(
            base_workspace=Path(run["workspace"]),
            product_contract=run["product_contract"],
            destination=workspace,
        )
        context_excerpt = self._context_artifact_excerpt(workspace, staged_context)
        failed_task_id = entry["task_id"]
        failed_worker = entry.get("worker_id") or role_owner
        failed_class = entry.get("failure_class")
        previous_path = self._artifact_if_present(entry, "primary.md")
        handoff = prepare_handoff(
            run=run, phase="primary", role_id=entry["role_id"],
            role_owner=role_owner, old_entry=entry,
            from_worker=failed_worker, to_worker=fallback,
            to_workspace=workspace,
            reason=failed_class or "runtime_failure",
            attempt=attempt_number,
            previous_artifact=(
                str(previous_path) if previous_path
                and self._artifact_ready_path(previous_path) else None
            ),
        )
        task = self.bridge.submit(
            agent_id=fallback,
            title=f"[{run['run_id']}] Fallback {entry['role_label']} via {fallback}",
            prompt=self._primary_prompt(
                run["goal"],
                role_owner,
                fallback,
                run["product_contract"],
                context_excerpt=context_excerpt,
            ) + "\n\n" + handoff["prompt_excerpt"],
            workspace=str(workspace),
            metadata={
                "team_run_id": run["run_id"],
                "team_phase": "primary",
                "team_role": entry["role_id"],
                "team_role_label": entry["role_label"],
                "preferred_worker": role_owner,
                "current_worker": fallback,
                "fallback_from_worker": failed_worker,
                "fallback_from_task": failed_task_id,
                "fallback_reason": failed_class,
                "fallback_plan_id": fallback_plan_id,
                "reassignment_count": attempt_number,
                "handoff_manifest": handoff["manifest"],
                "handoff_document": handoff["document"],
                "execution_budget": self._execution_budget(run, "primary"),
                "task_contract": {**run["product_contract"], "role_id": entry["role_id"], "role_label": entry["role_label"]},
                "expected_artifacts": ["primary.md"],
                "context_artifacts": staged_context,
                "context_excerpt": context_excerpt,
            },
        )
        handoff["record"].update({
            "new_task_id": task["task_id"],
            "status": "assigned",
            "manifest": handoff["manifest"],
        })
        save_handoff_state(handoff["record"])
        run.setdefault("handoffs", []).append(handoff["record"])
        entry["reassignment_count"] = attempt_number
        entry["fallback_from_worker"] = failed_worker
        entry["fallback_from_task"] = failed_task_id
        entry["fallback_reason"] = failed_class
        entry["worker_id"] = fallback
        entry["fallback_worker"] = fallback
        role_state = run.setdefault("roles", {}).setdefault(entry["role_id"], {})
        role_state["current_worker"] = fallback
        role_state["reassignment_count"] = attempt_number
        role_state["fallback_reason"] = failed_class
        role_state["fallback_from_worker"] = failed_worker
        entry["task_id"] = task["task_id"]
        entry["workspace"] = str(workspace)
        entry["status"] = task["status"]
        entry["started"] = bool(task.get("started"))
        entry["started_at"] = task.get("started_at")
        entry["finished_at"] = task.get("finished_at")
        entry["error"] = task.get("error")
        entry["failure_class"] = None
        entry["artifact"] = None
        entry.pop("salvaged", None)
        entry.pop("salvaged_artifact", None)
        entry.setdefault("attempts", []).append(
            {
                "worker_id": fallback,
                "task_id": task["task_id"],
                "workspace": str(workspace),
                "status": task["status"],
                "failure_class": None,
                "fallback_from_worker": failed_worker,
                "fallback_from_task": failed_task_id,
                "fallback_reason": failed_class,
                "fallback_plan_id": fallback_plan_id,
            }
        )
        run.setdefault("reassignments", []).append(
            {
                "role_id": entry["role_id"],
                "role_owner": role_owner,
                "original_worker": entry.get("original_worker"),
                "failed_worker": failed_worker,
                "failed_task_id": failed_task_id,
                "failure_class": failed_class,
                "fallback_worker": fallback,
                "fallback_task_id": task["task_id"],
                "fallback_plan_id": fallback_plan_id,
                "attempt": attempt_number,
                "at": _utcnow(),
            }
        )
        run.setdefault("warnings", []).append(
            f"reassigned role {entry['role_id']} from {failed_worker} to {fallback} "
            f"after {failed_class or 'unknown_failure'}"
        )
        return True

    def _reassign_failed_primary_roles(self, run: dict[str, Any]) -> bool:
        role_owners: list[str] = []
        for role_owner, entry in run.get("primary", {}).items():
            if self._salvage_entry(entry, "primary.md"):
                run.setdefault("warnings", []).append(
                    f"salvaged primary.md from terminal task {entry['task_id']}"
                )
                continue
            if (
                entry.get("reassignment_count", 0) < 1
                and entry.get("status") in {"failed", "cancelled"}
                and self._retryable_failure(entry)
                and not self._artifact_ready(entry, "primary.md")
            ):
                role_owners.append(role_owner)

        if not role_owners:
            return False

        fallback_policy = str(
            (run.get("product_contract") or {}).get("fallback_policy")
            or "reassign|salvage|degrade"
        )
        fallback_modes = {
            item.strip().casefold()
            for item in fallback_policy.split("|")
            if item.strip()
        }
        if "reassign" not in fallback_modes:
            # 中文说明：止损型 / 低价值验证任务可以显式选择 salvage|degrade，
            # 这时 Worker 失败后保留 coverage gap（覆盖缺口）继续收敛，
            # 不为了“凑齐角色”再自动烧一轮模型预算。
            warning = (
                "primary reassignment skipped by fallback policy "
                f"{fallback_policy!r}; degraded convergence is allowed"
            )
            if warning not in run.setdefault("warnings", []):
                run["warnings"].append(warning)
            return False

        plan = self._plan_primary_reassignments(run, role_owners)
        run.setdefault("fallback_plans", []).append(plan)
        # 先持久化完整计划，再提交任何 fallback 任务，保证调度 provenance 可审计。
        self.runs.save(run)

        reassigned = False
        for assignment in plan["assignments"]:
            role_owner = assignment["role_owner"]
            if self._reassign_primary_role(
                run,
                role_owner,
                fallback=assignment["assigned_worker"],
                fallback_plan_id=plan["plan_id"],
            ):
                reassigned = True
                assignment["status"] = "submitted"
                assignment["fallback_task_id"] = run["primary"][role_owner]["task_id"]
            else:
                assignment["status"] = "submission_skipped"

        submitted = sum(
            1 for item in plan["assignments"] if item["status"] == "submitted"
        )
        if submitted == len(plan["assignments"]) and not plan["unassigned"]:
            plan["status"] = "submitted"
        elif submitted:
            plan["status"] = "partial"
        else:
            plan["status"] = "unassigned"
        plan["submitted_at"] = _utcnow()
        if reassigned:
            run["phase_started_at"] = _utcnow()
        return reassigned

    def _cancel_unavailable_queued_primary(self, run: dict[str, Any]) -> list[str]:
        """持续不可用且从未启动的 queued task（排队任务）应提前转入重分工。"""
        health = self.worker_health_snapshot(run)
        observed = run.setdefault("unavailable_observed", {})
        grace = float(
            run.get("policy", {}).get("unavailable_reassign_grace_seconds", 20)
        )
        cancelled: list[str] = []
        for role_owner, entry in (run.get("primary") or {}).items():
            worker_id = str(entry.get("worker_id") or role_owner)
            # 只处理从未开始的排队任务；running（运行中）仍交给正常软/硬超时机制。
            if entry.get("status") != "queued" or entry.get("started"):
                observed.pop(role_owner, None)
                continue
            worker_health = health.get(worker_id) or {}
            if worker_health.get("global_status") == "available":
                observed.pop(role_owner, None)
                continue

            first_seen = observed.get(role_owner)
            if not first_seen:
                first_seen = _utcnow()
                observed[role_owner] = first_seen
                if grace > 0:
                    continue
            if _age_seconds(first_seen) < grace:
                continue

            try:
                self.bridge.cancel(entry["task_id"])
            except Exception as exc:
                run.setdefault("warnings", []).append(
                    f"unavailable queued-task cancellation failed for {entry['task_id']}: {exc}"
                )
                continue
            self._refresh_entry(entry)
            entry["failure_class"] = "model_unavailable"
            entry["error"] = "worker became globally unavailable before task lease"
            attempts = entry.get("attempts") or []
            if attempts and attempts[-1].get("task_id") == entry.get("task_id"):
                attempts[-1]["status"] = entry.get("status")
                attempts[-1]["failure_class"] = "model_unavailable"
                attempts[-1]["error"] = entry["error"]
            cancelled.append(role_owner)
            observed.pop(role_owner, None)

        if cancelled:
            run.setdefault("warnings", []).append(
                "cancelled globally unavailable queued primary role(s) for immediate reassignment: "
                + ", ".join(run["primary"][owner]["role_id"] for owner in cancelled)
            )
        return cancelled

    def _resolve_artifact(self, entry: dict[str, Any], filename: str) -> Path:
        path = Path(entry["workspace"]) / filename
        if not path.is_file():
            raise FileNotFoundError(f"expected completed artifact missing: {path}")
        return path

    def _stage_primary_inputs(self, run: dict[str, Any], destination: Path) -> list[str]:
        inputs = destination / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        staged: list[str] = []
        for role_owner in self._usable(run["primary"], "primary.md"):
            entry = run["primary"][role_owner]
            source = self._resolve_artifact(entry, "primary.md") if entry.get("status") == "completed" else Path(entry["salvaged_artifact"])
            target = inputs / f"primary-{entry['role_id']}.md"
            shutil.copy2(source, target)
            staged.append(target.name)
            entry["artifact"] = str(source)
            canonical = str(source)
            for attempt_artifact in self._attempt_artifacts(entry, "primary.md"):
                if not attempt_artifact.get("ready") or attempt_artifact["path"] == canonical:
                    continue
                extra = inputs / (
                    f"primary-{entry['role_id']}-attempt{attempt_artifact['attempt']}-"
                    f"{attempt_artifact['worker_id']}.md"
                )
                shutil.copy2(attempt_artifact["path"], extra)
                staged.append(extra.name)
        return staged

    @staticmethod
    def _synthesis_finalization_context(
        synth_workspace: Path,
        staged_files: list[str],
        run: dict[str, Any],
        *,
        max_chars: int = 60000,
    ) -> str:
        """把已暂存证据嵌入收尾上下文，保证零工具 Finalization 仍能做真实综合。"""
        lines = [
            "SYNTHESIS STAGED EVIDENCE (authoritative, captured before finalization):",
            f"Run degraded: {bool(run.get('degraded'))}",
            f"Coverage gaps: {json.dumps(run.get('coverage_gaps') or {}, ensure_ascii=False)}",
        ]
        remaining = max_chars
        for name in staged_files:
            path = synth_workspace / "inputs" / name
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as exc:
                text = f"READ ERROR: {exc}"
            # 中文说明：收尾上下文必须有界，避免大报告把模型上下文全部挤满；
            # 文件原件仍保留在 inputs/，正常探索阶段可按需读取完整内容。
            excerpt = text[: min(12000, remaining)]
            lines.extend([f"--- BEGIN {name} ---", excerpt, f"--- END {name} ---"])
            remaining -= len(excerpt)
            if remaining <= 0:
                lines.append("[SYNTHESIS CONTEXT TRUNCATED: use only evidence above during zero-tool finalization]")
                break
        return "\n".join(lines)

    @staticmethod
    def _review_contract_valid(path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return False
        if not all(marker in text for marker in REVIEW_REQUIRED_MARKERS):
            return False
        verdict = next((line.strip().casefold() for line in text.splitlines() if line.strip().casefold().startswith("verdict:")), "")
        return verdict in {
            "verdict: accept",
            "verdict: repair",
            "verdict: insufficient_evidence",
        }

    def _usable_reviews(self, run: dict[str, Any]) -> list[str]:
        usable: list[str] = []
        for review_id, entry in run.get("reviews", {}).items():
            path: Path | None = None
            if entry.get("status") == "completed":
                path = self._artifact_if_present(entry, "review.md")
            elif entry.get("salvaged"):
                path = self._artifact_if_present(entry, "review.md")
            if not path:
                continue
            valid = self._review_contract_valid(path)
            entry["review_contract_version"] = REVIEW_CONTRACT_VERSION
            entry["review_contract_valid"] = valid
            if valid:
                usable.append(review_id)
            else:
                run.setdefault("warnings", []).append(
                    f"review artifact {review_id} failed {REVIEW_CONTRACT_VERSION} structure gate"
                )
        return usable

    def _review_pairs(self, run: dict[str, Any], successful: list[str]) -> list[tuple[str, str]]:
        active = [role_owner for role_owner in run["participants"] if role_owner in successful]
        if not active:
            return []

        eligible_reviewers = set(self._eligible_workers(run))
        producers = {
            (run["primary"][role_owner].get("worker_id") or role_owner)
            for role_owner in active
        }
        if len(active) >= 2 and len(producers) >= 2:
            # Preserve the original ring semantics whenever genuinely different
            # producers contributed usable artifacts.
            pairs: list[tuple[str, str]] = []
            for index, reviewer_role in enumerate(active):
                reviewer_worker = run["primary"][reviewer_role].get("worker_id") or reviewer_role
                target_role = active[index - 1]
                target_worker = run["primary"][target_role].get("worker_id") or target_role
                if reviewer_worker != target_worker and reviewer_worker in eligible_reviewers:
                    pairs.append((reviewer_worker, target_role))
                    continue
                # A localized fallback collision can still happen inside an
                # otherwise diverse ring. Find the nearest different producer.
                for offset in range(2, len(active) + 1):
                    candidate = active[(index - offset) % len(active)]
                    candidate_worker = run["primary"][candidate].get("worker_id") or candidate
                    if (
                        candidate_worker != reviewer_worker
                        and reviewer_worker in eligible_reviewers
                    ):
                        pairs.append((reviewer_worker, candidate))
                        break
                else:
                    # 原生产者本轮不健康时，改用健康且独立的 Reviewer。
                    for external_worker in self._eligible_workers(run):
                        if external_worker != target_worker:
                            pairs.append((external_worker, target_role))
                            break
            return pairs

        # All usable role artifacts collapsed onto one actual producer. Review
        # must still be independent, so recruit reviewers from the full available
        # worker pool instead of producing an empty review phase.
        target_worker = next(iter(producers))
        external = [
            worker_id
            for worker_id in self._eligible_workers(run)
            if worker_id != target_worker
        ]
        if not external:
            return []
        pairs = []
        for index, target_role in enumerate(active):
            pairs.append((external[index % len(external)], target_role))
        return pairs

    def _review_prompt(
        self,
        run: dict[str, Any],
        reviewer: str,
        target: str,
        *,
        target_excerpt: str = "",
    ) -> str:
        target_entry = run["primary"][target]
        return f"""You are the cross-reviewer in a multi-agent team run.

Shared goal:
{run['goal']}

Primary report you must review:
inputs/primary-{target_entry['role_id']}.md

Other primary reports in `inputs/` are comparison context.

Authoritative target excerpt（权威目标摘要；即使文件工具失效也必须审查这些内容）:
--- BEGIN TARGET PRIMARY EXCERPT ---
{target_excerpt or '[excerpt unavailable; read the staged file]'}
--- END TARGET PRIMARY EXCERPT ---

Original acceptance checks: {json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}
Evidence requirements: {json.dumps(run.get('product_contract', {}).get('evidence_required') or [], ensure_ascii=False)}

Audit the target independently for:
1. unsupported claims or missing evidence,
2. contradictions with the other primary reports,
3. implementation/operational risks,
4. important omissions,
5. concrete corrections or validation steps.

Reviewer worker: {reviewer}
Target role: {target_entry['role_id']} ({target_entry['role_label']})
Target worker that produced the artifact: {target_entry.get('worker_id')}

Create `review.md`. It is mandatory. Do not edit the staged input files.
The file must use this exact review contract structure:

## ACCEPTED_CHECKS
- ...

## FAILED_CHECKS
- ...

## COUNTER_EVIDENCE
- ...

## UNSUPPORTED_CLAIMS
- ...

## REQUIRED_REPAIR
- ...

VERDICT: accept | repair | insufficient_evidence
"""

    def _record_primary_coverage(self, run: dict[str, Any], successful: list[str]) -> None:
        missing = [
            run["primary"][role_owner]["role_id"]
            for role_owner in run.get("participants", [])
            if role_owner not in successful
        ]
        coverage = run.setdefault("coverage_gaps", {})
        if missing:
            coverage["missing_primary_roles"] = missing
        else:
            coverage.pop("missing_primary_roles", None)
        if not coverage:
            run.pop("coverage_gaps", None)

    def _start_reviews(self, run: dict[str, Any]) -> dict[str, Any]:
        successful = self._usable(run["primary"], "primary.md")
        self._record_primary_coverage(run, successful)
        minimum = int(run.get("policy", {}).get("min_primary_success", min(3, len(run["participants"]))))
        allow_degraded = bool(run.get("policy", {}).get("allow_degraded_synthesis", True))
        if len(successful) < minimum:
            if not successful or not allow_degraded:
                run["status"] = "failed"
                run["phase"] = "failed"
                run["errors"].append(
                    f"only {len(successful)} usable primary artifacts; minimum is {minimum}"
                )
                return run
            run["degraded"] = True
            run.setdefault("warnings", []).append(
                f"primary gate degraded: {len(successful)} usable artifacts below minimum {minimum}"
            )
            if len(successful) < 2:
                return self._start_synthesis(run, allow_no_reviews=True)
        run_root = Path(run["run_root"])
        review_pairs = self._review_pairs(run, successful)
        if not review_pairs:
            # 没有健康且独立的 Reviewer 时不能进入空 review_running 死锁。
            # 允许降级时继续综合，但必须把“0 条独立审查”作为覆盖缺口显式保留。
            minimum_reviews = int(
                run.get("policy", {}).get(
                    "min_review_success",
                    min(2, len(run["participants"])),
                )
            )
            coverage = run.setdefault("coverage_gaps", {})
            coverage["missing_review_count"] = minimum_reviews
            run["degraded"] = True
            run.setdefault("warnings", []).append(
                "review gate degraded: no healthy independent reviewer available"
            )
            if allow_degraded:
                return self._start_synthesis(run, allow_no_reviews=True)
            run["status"] = "failed"
            run["phase"] = "failed"
            run["errors"].append("no healthy independent reviewer available")
            return run

        for reviewer, target in review_pairs:
            target_entry = run["primary"][target]
            review_id = f"{reviewer}-reviews-{target_entry['role_id']}"
            review_workspace = run_root / "reviews" / review_id
            review_workspace.mkdir(parents=True, exist_ok=True)
            self._stage_primary_inputs(run, review_workspace)
            target_path = (
                review_workspace
                / "inputs"
                / f"primary-{target_entry['role_id']}.md"
            )
            try:
                # 中文说明：审查不能完全依赖 Worker 的文件工具。把目标成果的有界摘要直接
                # 嵌入 prompt；完整文件仍保留在 inputs/ 供正常审查读取。
                target_excerpt = target_path.read_text(encoding="utf-8")[:12000]
            except Exception as exc:
                target_excerpt = f"[TARGET EXCERPT READ ERROR: {exc}]"
            task = self.bridge.submit(
                agent_id=reviewer,
                title=f"[{run['run_id']}] Review {target_entry['role_label']} by {reviewer}",
                prompt=self._review_prompt(
                    run,
                    reviewer,
                    target,
                    target_excerpt=target_excerpt,
                ),
                workspace=str(review_workspace),
                metadata={
                    "team_run_id": run["run_id"],
                    "team_phase": "review",
                    "reviewer": reviewer,
                    "target_role": target_entry["role_id"],
                    "target_worker": target_entry.get("worker_id"),
                    "review_contract": REVIEW_CONTRACT_VERSION,
                    "acceptance": run.get("product_contract", {}).get("acceptance") or [],
                    "execution_budget": self._execution_budget(run, "review"),
                    # 中文说明：Review 也可能被软截止切换到新的 Finalization 会话。
                    # target_excerpt 必须进入 finalization_context，否则收尾会话会“忘记被审成果”。
                    "finalization_context": target_excerpt,
                    "expected_artifacts": ["review.md"],
                },
            )
            run["reviews"][review_id] = {
                "reviewer": reviewer,
                "target": target,
                "target_role": target_entry["role_id"],
                "target_worker": target_entry.get("worker_id"),
                "task_id": task["task_id"],
                "workspace": str(review_workspace),
                "status": task["status"],
                "started": bool(task.get("started")),
                "artifact": None,
            }
        run["status"] = "review_running"
        run["phase"] = "review"
        run["phase_started_at"] = _utcnow()
        return run

    def _select_synthesizer(self, run: dict[str, Any]) -> str:
        eligible = set(self._eligible_workers(run))
        contributors = [
            run["primary"][role_owner].get("worker_id") or role_owner
            for role_owner in self._usable(run["primary"], "primary.md")
        ]
        for agent_id in SYNTHESIS_PREFERENCE:
            if agent_id in eligible and agent_id in contributors:
                return agent_id
        # 如果贡献者本轮都已降级/不健康，允许健康 Worker 基于阶段化证据做综合。
        for agent_id in SYNTHESIS_PREFERENCE:
            if agent_id in eligible:
                return agent_id
        raise RuntimeError("no healthy worker available for synthesis")

    def _start_synthesis(self, run: dict[str, Any], *, allow_no_reviews: bool = False) -> dict[str, Any]:
        completed_reviews = self._usable_reviews(run) if run.get("reviews") else []
        minimum = int(run.get("policy", {}).get("min_review_success", min(2, len(run["participants"]))))
        allow_degraded = bool(run.get("policy", {}).get("allow_degraded_synthesis", True))
        if len(completed_reviews) < minimum and not allow_no_reviews:
            if not allow_degraded:
                run["status"] = "failed"
                run["phase"] = "failed"
                run["errors"].append(
                    f"only {len(completed_reviews)} usable reviews; minimum is {minimum}"
                )
                return run
            run["degraded"] = True
            run.setdefault("coverage_gaps", {})["missing_review_count"] = max(0, minimum - len(completed_reviews))
            run.setdefault("warnings", []).append(
                f"review gate degraded: {len(completed_reviews)} usable reviews below minimum {minimum}"
            )
        synth_agent = self._select_synthesizer(run)
        synth_workspace = Path(run["run_root"]) / "synthesis"
        synth_workspace.mkdir(parents=True, exist_ok=True)
        staged_primary = self._stage_primary_inputs(run, synth_workspace)
        review_inputs = synth_workspace / "inputs"
        staged_reviews: list[str] = []
        for review_id in completed_reviews:
            entry = run["reviews"][review_id]
            source = self._resolve_artifact(entry, "review.md") if entry.get("status") == "completed" else Path(entry["salvaged_artifact"])
            target = review_inputs / f"review-{review_id}.md"
            shutil.copy2(source, target)
            staged_reviews.append(target.name)
            entry["artifact"] = str(source)
        finalization_context = self._synthesis_finalization_context(
            synth_workspace,
            staged_primary + staged_reviews,
            run,
        )
        initial_context = self._synthesis_finalization_context(
            synth_workspace,
            staged_primary + staged_reviews,
            run,
            max_chars=24000,
        )
        prompt = f"""You are the synthesis editor for a completed multi-agent run.

Shared goal:
{run['goal']}

The `inputs/` directory contains independent primary reports and cross-reviews.
Primary files: {', '.join(staged_primary)}
Review files: {', '.join(staged_reviews)}
Run degraded: {bool(run.get('degraded'))}
Coverage gaps: {json.dumps(run.get('coverage_gaps') or {}, ensure_ascii=False)}
Product acceptance checks: {json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}

Bounded authoritative evidence excerpt（有界权威证据摘要）:
{initial_context}

Use the bounded evidence above first. Only open staged files when one material acceptance decision cannot be made from the embedded evidence. Do not reread the same file repeatedly.

Produce a decision-quality synthesis that:
- identifies agreements and material disagreements,
- distinguishes evidence from assumptions,
- incorporates valid reviewer corrections,
- records unresolved questions instead of hiding them,
- gives concrete prioritized next actions,
- preserves minority/contrarian findings when they are evidence-backed,
- explicitly reports missing roles/reviews when this is a degraded run.

Create `final.md`. It is mandatory. Do not edit input files.
"""
        task = self.bridge.submit(
            agent_id=synth_agent,
            title=f"[{run['run_id']}] Final team synthesis",
            prompt=prompt,
            workspace=str(synth_workspace),
            metadata={
                "team_run_id": run["run_id"],
                "team_phase": "synthesis",
                "execution_budget": self._execution_budget(run, "synthesis"),
                "finalization_context": finalization_context,
                "expected_artifacts": ["final.md"],
            },
        )
        run["synthesis"] = {
            "agent_id": synth_agent,
            "task_id": task["task_id"],
            "workspace": str(synth_workspace),
            "status": task["status"],
            "started": bool(task.get("started")),
            "artifact": None,
        }
        run["status"] = "synthesis_running"
        run["phase"] = "synthesis"
        run["phase_started_at"] = _utcnow()
        return run

    def _select_implementation_worker(self, run: dict[str, Any]) -> str:
        eligible = set(self._eligible_workers(run))
        for worker_id in IMPLEMENTATION_PREFERENCE:
            if worker_id in eligible:
                return worker_id
        raise RuntimeError("no healthy worker available for implementation gate")

    def _start_implementation(self, run: dict[str, Any], final_path: Path) -> dict[str, Any]:
        worker_id = self._select_implementation_worker(run)
        product_workspace = Path(run["workspace"])
        report_rel = Path(".ai-team-runs") / run["run_id"] / "implementation" / IMPLEMENTATION_REPORT
        report_abs = product_workspace / report_rel
        report_abs.parent.mkdir(parents=True, exist_ok=True)
        deliverables = list(run.get("product_contract", {}).get("deliverable_artifacts") or [])
        deliverable_lines = "\n".join(f"- {item}" for item in deliverables) or "- None declared"
        prompt = f"""You are the implementation owner for an AI product-team run.

Shared goal:
{run['goal']}

Final synthesis to execute:
{final_path}

Product workspace you may modify:
{product_workspace}

Run-start workspace baseline (运行开始时基线):
{run.get('workspace_baseline', {}).get('metadata_file')}

Baseline rules:
- Treat the run-start baseline as the truth for pre-existing dirty-worktree changes.
- Do NOT use Git HEAD alone to decide whether a behavior/change was introduced by this run.
- For tracked files, run-start state is HEAD plus the saved tracked.patch.
- For untracked files, use the saved manifest/hash to determine whether the file already existed before this run.

Product acceptance checks:
{json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}

Mandatory product deliverables（强制产品成果）:
{deliverable_lines}

Execution rules:
- Read the final synthesis and current git/worktree state before editing.
- Apply only changes that the synthesis actually requires. If the synthesis accepts the current implementation and no product edit is required, do not invent churn: perform a NO_CHANGE verification instead.
- Every declared mandatory product deliverable above MUST exist and be complete before this phase can succeed. A report alone is not delivery.
- If a mandatory Markdown deliverable contains AI_TEAM_ARTIFACT_STATUS: in_progress, replace it with completed content before finishing.
- Never commit or push in this phase.
- Preserve unrelated dirty-worktree changes.
- Run the narrow declared acceptance tests plus the required regression suite that is practical for the product.
- Record exact changed files and exact test commands/results.
- Write `{report_rel}`. That report is mandatory.

The report must contain:
IMPLEMENTATION_STATUS: changed | no_change | blocked
## CHANGED_FILES
## ACCEPTANCE_EVIDENCE
## TEST_RESULTS
## UNRESOLVED
"""
        task = self.bridge.submit(
            agent_id=worker_id,
            title=f"[{run['run_id']}] Implement accepted product decision",
            prompt=prompt,
            workspace=str(product_workspace),
            metadata={
                "team_run_id": run["run_id"],
                "team_phase": "implementation",
                "final_synthesis": str(final_path),
                "product_workspace": str(product_workspace),
                "workspace_baseline": run.get("workspace_baseline"),
                "execution_budget": self._execution_budget(run, "implementation"),
                "expected_artifacts": [str(report_rel), *deliverables],
                "deliverable_artifacts": deliverables,
            },
        )
        run["implementation"] = {
            "agent_id": worker_id,
            "task_id": task["task_id"],
            "workspace": str(product_workspace),
            "artifact_relative": str(report_rel),
            "status": task["status"],
            "started": bool(task.get("started")),
            "artifact": None,
        }
        run["status"] = "implementation_running"
        run["phase"] = "implementation"
        run["phase_started_at"] = _utcnow()
        return run

    def _implementation_artifact(self, entry: dict[str, Any]) -> Path:
        path = Path(entry["workspace"]) / entry.get("artifact_relative", IMPLEMENTATION_REPORT)
        if not path.is_file():
            raise FileNotFoundError(f"expected implementation artifact missing: {path}")
        return path

    def _missing_product_deliverables(self, run: dict[str, Any]) -> list[str]:
        """独立于 Worker 元数据，再检查一次真正产品成果是否已经落盘并完成。"""
        workspace = Path(run["workspace"])
        missing: list[str] = []
        for relative in run.get("product_contract", {}).get("deliverable_artifacts") or []:
            path = workspace / str(relative)
            if not self._artifact_ready_path(path):
                missing.append(str(relative))
        return missing

    def _select_validation_worker(
        self,
        run: dict[str, Any],
        implementation_worker: str,
        *,
        exclude: set[str] | None = None,
    ) -> str:
        eligible = set(self._eligible_workers(run))
        excluded = set(exclude or set()) | {implementation_worker}
        for worker_id in VALIDATION_PREFERENCE:
            if worker_id in eligible and worker_id not in excluded:
                return worker_id
        raise RuntimeError("no healthy independent worker available for implementation validation")

    @staticmethod
    def _declared_validation_commands(implementation_path: Path, *, limit: int = 4) -> list[str]:
        try:
            text = implementation_path.read_text(encoding="utf-8")
        except Exception:
            return []
        commands: list[str] = []
        accepted_prefixes = (
            "python ", "python3 ", "pytest", "npm test", "npm run test",
            "node --test", "pnpm test", "yarn test", "cargo test", "go test",
        )
        for command in re.findall(r"`([^`\n]+)`", text):
            value = command.strip()
            lower = value.casefold()
            if not lower.startswith(accepted_prefixes):
                continue
            if value not in commands:
                commands.append(value)
            if len(commands) >= limit:
                break
        return commands

    @staticmethod
    def _declared_changed_files(implementation_path: Path, workspace: Path) -> list[str]:
        try:
            text = implementation_path.read_text(encoding="utf-8")
        except Exception:
            return []
        match = re.search(r"## CHANGED_FILES\s*(.*?)(?=\n## |\Z)", text, re.S)
        if not match:
            return []
        files: list[str] = []
        section = match.group(1)
        candidates: list[str] = []
        for line in section.splitlines():
            table_match = re.match(r"\s*\|\s*`([^`]+)`\s*\|", line)
            bullet_match = re.match(r"\s*[-*]\s+`([^`]+)`(?:\s|$)", line)
            found = table_match or bullet_match
            if found:
                candidates.append(found.group(1).strip())
        for candidate in candidates:
            if not candidate or candidate.casefold() in {"none", "n/a"}:
                continue
            if candidate.startswith(workspace.name + "/"):
                candidate = candidate[len(workspace.name) + 1:]
            path = Path(candidate)
            if path.is_absolute() or ".." in path.parts:
                continue
            # A validation file target must look like a file, not a code symbol/command.
            if not path.suffix:
                continue
            if candidate not in files:
                files.append(candidate)
        return files

    @staticmethod
    def _safe_validation_argv(command: str) -> list[str] | None:
        try:
            argv = shlex.split(command)
        except ValueError:
            return None
        if not argv:
            return None
        if argv[0] in {"python", "python3"}:
            return argv if len(argv) >= 3 and argv[1:3] == ["-m", "unittest"] else None
        if argv[0] in {"pytest", "py.test"}:
            return argv
        if argv[0] == "node":
            return argv if len(argv) >= 2 and argv[1] == "--test" else None
        if argv[0] in {"npm", "pnpm", "yarn"}:
            if len(argv) >= 2 and argv[1] == "test":
                return argv
            if argv[0] == "npm" and len(argv) >= 3 and argv[1:3] == ["run", "test"]:
                return argv
            return None
        if argv[0] == "cargo":
            return argv if len(argv) >= 2 and argv[1] == "test" else None
        if argv[0] == "go":
            return argv if len(argv) >= 2 and argv[1] == "test" else None
        return None

    @staticmethod
    def _resolved_validation_argv(argv: list[str]) -> list[str]:
        if not argv:
            return argv
        resolved = list(argv)
        if resolved[0] in {"python", "python3"}:
            override = os.getenv("AI_TEAM_VALIDATION_PYTHON", "").strip()
            resolved[0] = override or sys.executable
        return resolved

    def _run_deterministic_validation_harness(
        self,
        run: dict[str, Any],
        implementation_path: Path,
        *,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        workspace = Path(run["workspace"])
        commands = self._declared_validation_commands(implementation_path)
        results: list[dict[str, Any]] = []
        for command in commands:
            argv = self._safe_validation_argv(command)
            if not argv:
                results.append({
                    "command": command,
                    "status": "rejected",
                    "reason": "command is outside deterministic validation allowlist",
                })
                continue
            executed_argv = self._resolved_validation_argv(argv)
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    executed_argv,
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                results.append({
                    "command": command,
                    "argv": argv,
                    "executed_argv": executed_argv,
                    "runtime": executed_argv[0],
                    "status": "passed" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "stdout_tail": completed.stdout[-6000:],
                    "stderr_tail": completed.stderr[-6000:],
                })
            except subprocess.TimeoutExpired as exc:
                results.append({
                    "command": command,
                    "argv": argv,
                    "executed_argv": executed_argv,
                    "runtime": executed_argv[0],
                    "status": "timeout",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "stdout_tail": str(exc.stdout or "")[-6000:],
                    "stderr_tail": str(exc.stderr or "")[-6000:],
                })
        changed_files = self._declared_changed_files(implementation_path, workspace)
        diff_scope: dict[str, Any] = {"declared_files": changed_files}
        if changed_files:
            for name, argv in {
                "status": ["git", "status", "--short", "--", *changed_files],
                "diff_stat": ["git", "diff", "--stat", "--", *changed_files],
                "diff_check": ["git", "diff", "--check", "--", *changed_files],
            }.items():
                completed = subprocess.run(
                    argv, cwd=workspace, text=True, capture_output=True, timeout=30, check=False,
                )
                diff_scope[name] = {
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-6000:],
                    "stderr": completed.stderr[-3000:],
                }
        evidence = {
            "version": "deterministic-validation-v0.1",
            "run_id": run["run_id"],
            "workspace": str(workspace),
            "implementation_artifact": str(implementation_path),
            "commands_declared": commands,
            "results": results,
            "all_declared_passed": bool(commands) and all(item.get("status") == "passed" for item in results),
            "diff_scope": diff_scope,
            "created_at": _utcnow(),
        }
        evidence_path = Path(run["run_root"]) / "validation" / "deterministic_evidence.json"
        _atomic_json(evidence_path, evidence)
        run["deterministic_validation"] = {
            "artifact": str(evidence_path),
            "all_declared_passed": evidence["all_declared_passed"],
            "result_count": len(results),
        }
        return evidence

    def _validation_finalization_context(
        self,
        run: dict[str, Any],
        implementation_path: Path,
    ) -> str:
        deterministic = run.get("deterministic_validation") or {}
        lines = [
            f"Implementation report: {implementation_path}",
            f"Acceptance checks: {json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}",
            f"Mandatory product deliverables: {json.dumps(run.get('product_contract', {}).get('deliverable_artifacts') or [], ensure_ascii=False)}",
            f"Coverage gaps: {json.dumps(run.get('coverage_gaps') or {}, ensure_ascii=False)}",
        ]
        try:
            implementation_text = implementation_path.read_text(encoding="utf-8")
            status_match = re.search(r"IMPLEMENTATION_STATUS:\s*([^\n]+)", implementation_text)
            if status_match:
                lines.append(f"Implementation status claim: {status_match.group(1).strip()}")
            evidence_match = re.search(
                r"## ACCEPTANCE_EVIDENCE\s*(.*?)(?=\n## |\Z)",
                implementation_text,
                re.S,
            )
            if evidence_match:
                lines.append(
                    "Implementation acceptance claims (claims only; compare against deterministic evidence):\n"
                    + evidence_match.group(1).strip()[:5000]
                )
        except Exception as exc:
            lines.append(f"Implementation report read error: {exc}")
        evidence_path = deterministic.get("artifact")
        if evidence_path:
            lines.append(f"Deterministic evidence artifact: {evidence_path}")
            lines.append(f"All declared test commands passed: {deterministic.get('all_declared_passed')}")
            try:
                payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
                for item in payload.get("results", []):
                    tail = (item.get("stderr_tail") or item.get("stdout_tail") or "").strip()[-1000:]
                    lines.append(
                        f"TEST {item.get('status')}: {item.get('command')} | returncode={item.get('returncode')} | runtime={item.get('runtime')} | output={tail}"
                    )
                lines.append(
                    "Deterministic diff scope: "
                    + json.dumps(payload.get("diff_scope") or {}, ensure_ascii=False)
                )
            except Exception as exc:
                lines.append(f"Deterministic evidence read error: {exc}")
        return "\n".join(lines)

    def _validation_prompt(
        self,
        run: dict[str, Any],
        implementation_path: Path,
        report_rel: Path,
    ) -> str:
        product_workspace = Path(run["workspace"])
        declared_commands = self._declared_validation_commands(implementation_path)
        command_plan = "\n".join(f"- `{item}`" for item in declared_commands) or "- No executable test command was declared; record this as an evidence gap rather than searching broadly."
        deterministic = run.get("deterministic_validation") or {}
        deterministic_artifact = deterministic.get("artifact")
        deliverables = list(run.get("product_contract", {}).get("deliverable_artifacts") or [])
        deliverable_plan = "\n".join(f"- `{item}`" for item in deliverables) or "- No separate product deliverable declared."
        deterministic_summary = "Deterministic Supervisor validation evidence: not available"
        if deterministic_artifact:
            evidence_excerpt: dict[str, Any] = {
                "artifact": deterministic_artifact,
                "all_declared_passed": deterministic.get("all_declared_passed"),
                "results": [],
            }
            try:
                evidence_payload = json.loads(Path(deterministic_artifact).read_text(encoding="utf-8"))
                evidence_excerpt["results"] = [
                    {
                        "command": item.get("command"),
                        "status": item.get("status"),
                        "returncode": item.get("returncode"),
                        "runtime": item.get("runtime"),
                        "duration_seconds": item.get("duration_seconds"),
                        "output_tail": (item.get("stderr_tail") or item.get("stdout_tail") or "")[-1200:],
                    }
                    for item in evidence_payload.get("results", [])
                ]
            except Exception as exc:
                evidence_excerpt["read_error"] = str(exc)
            deterministic_summary = (
                "Deterministic Supervisor validation evidence (machine-executed, do not rerun):\n"
                + json.dumps(evidence_excerpt, ensure_ascii=False, indent=2)
            )
        return f"""You are the independent implementation validator for an AI product-team run.

Shared goal:
{run['goal']}

Final synthesis:
{run.get('final_artifact')}

Implementation report:
{implementation_path}

{deterministic_summary}

Product workspace to inspect read-only except for your validation report:
{product_workspace}

Run-start workspace baseline (运行开始时基线):
{run.get('workspace_baseline', {}).get('metadata_file')}

Baseline rules:
- Attribute regressions to this run only by comparing against the run-start baseline, not Git HEAD alone.
- For tracked files, the run-start state is HEAD plus the saved tracked.patch.
- A pre-existing dirty change is not an implementation regression merely because it differs from HEAD.
- For untracked files, consult the saved path/size/SHA-256 manifest before claiming the run created them.

Original acceptance checks:
{json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}

Mandatory product deliverables to validate（必须验收的产品成果）:
{deliverable_plan}

Validation execution plan (run these first, in order, from the product workspace):
{command_plan}

Validation rules:
- This is a bounded independent validation, not a new research task. Do NOT invoke sub-agents.
- If deterministic Supervisor validation evidence is available, read that evidence first and do NOT rerun commands already recorded there.
- Otherwise execute the declared validation commands above before any repository search.
- Read the implementation report; inspect only the explicitly named changed files needed to check its concrete claims.
- Inspect every mandatory product deliverable listed above. Missing, placeholder/in-progress, or acceptance-incomplete deliverables cannot receive `accept`.
- Do NOT use broad grep/list/search to rediscover the repository structure unless one acceptance check remains undecidable after the declared commands.
- Re-run the specific relevant tests declared by the implementation report; do not broaden into unrelated exploration.
- Do not trust implementation test claims without independently executing enough of them to decide the acceptance checks.
- As soon as the acceptance checks are decidable, write the report and stop.
- Do not modify product source files.
- Preserve unrelated dirty changes.
- Verdict semantics are strict: use `repair` when failed checks can be resolved by changing product code/tests or by producing missing executable evidence inside the current workspace.
- Use `blocked` only when no repair inside the current workspace can proceed because of an external/resource/permission dependency (for example: no independent validator, missing credentials, unavailable required environment).
- If you list one or more actionable items under REQUIRED_REPAIR, the verdict MUST be `repair`, not `blocked`.
- Write `{report_rel}` using exactly this structure:

## ACCEPTED_CHECKS
- ...
## FAILED_CHECKS
- ...
## DIFF_SCOPE
- ...
## TEST_EVIDENCE
- ...
## REQUIRED_REPAIR
- ...
VERDICT: accept | repair | blocked
"""

    def _start_validation(
        self,
        run: dict[str, Any],
        change_report_path: Path,
        *,
        change_worker: str | None = None,
        repair_attempt: int | None = None,
        reviewer_override: str | None = None,
    ) -> dict[str, Any]:
        """启动独立验证；既支持首次 Implementation，也支持 Repair 后复验。"""
        change_worker = change_worker or run["implementation"]["agent_id"]
        if reviewer_override is not None:
            if reviewer_override == change_worker:
                raise RuntimeError("validation reviewer override must be independent from change worker")
            by_id = {item["agent_id"]: item for item in self.bridge.agents()}
            state = by_id.get(reviewer_override) or {}
            if not (
                state.get("configured")
                and state.get("available")
                and state.get("queueable")
            ):
                raise RuntimeError(
                    f"validation reviewer override is not globally available: {reviewer_override}"
                )
            reviewer = reviewer_override
        else:
            try:
                reviewer = self._select_validation_worker(run, change_worker)
            except RuntimeError as exc:
                # 没有独立验证者是资源/覆盖阻塞，不应被误报成产品实现失败。
                coverage = run.setdefault("coverage_gaps", {})
                coverage["missing_independent_validator"] = True
                run["status"] = "blocked"
                run["phase"] = "blocked"
                run.setdefault("errors", []).append(str(exc))
                run.setdefault("warnings", []).append(
                    "validation gate blocked: no healthy independent validator available"
                )
                return run
        product_workspace = Path(run["workspace"])
        if repair_attempt is None:
            report_rel = Path(".ai-team-runs") / run["run_id"] / "validation" / VALIDATION_REPORT
        else:
            report_rel = (
                Path(".ai-team-runs") / run["run_id"] / "validation" /
                f"repair-{repair_attempt}" / VALIDATION_REPORT
            )
        report_abs = product_workspace / report_rel
        report_abs.parent.mkdir(parents=True, exist_ok=True)
        task = self.bridge.submit(
            agent_id=reviewer,
            title=(
                f"[{run['run_id']}] Validate repair attempt {repair_attempt}"
                if repair_attempt is not None
                else f"[{run['run_id']}] Validate implementation result"
            ),
            prompt=self._validation_prompt(run, change_report_path, report_rel),
            workspace=str(product_workspace),
            metadata={
                "team_run_id": run["run_id"],
                "team_phase": "validation",
                # 保留 implementation_worker 兼容旧消费者，同时新增更准确的 change_worker。
                "implementation_worker": change_worker,
                "change_worker": change_worker,
                "change_report": str(change_report_path),
                "workspace_baseline": run.get("workspace_baseline"),
                "repair_attempt": repair_attempt,
                "validation_attempt": 1,
                "finalization_context": self._validation_finalization_context(run, change_report_path),
                "execution_budget": self._execution_budget(run, "validation"),
                "expected_artifacts": [str(report_rel)],
            },
        )
        run["validation"] = {
            "agent_id": reviewer,
            "implementation_worker": change_worker,
            "change_worker": change_worker,
            "change_report": str(change_report_path),
            "repair_attempt": repair_attempt,
            "task_id": task["task_id"],
            "workspace": str(product_workspace),
            "artifact_relative": str(report_rel),
            "status": task["status"],
            "started": bool(task.get("started")),
            "artifact": None,
            "reassignment_count": 0,
            "attempts": [{
                "attempt": 1,
                "worker_id": reviewer,
                "task_id": task["task_id"],
                "artifact_relative": str(report_rel),
                "status": task["status"],
                "failure_class": None,
            }],
        }
        run["status"] = "validation_running"
        run["phase"] = "validation"
        run["phase_started_at"] = _utcnow()
        return run

    def _reassign_validation(self, run: dict[str, Any], change_report_path: Path) -> bool:
        entry = run.get("validation") or {}
        if not entry or entry.get("reassignment_count", 0) >= 1:
            return False
        if entry.get("status") not in {"failed", "cancelled"}:
            return False
        failure_class = entry.get("failure_class") or self._classify_failure(entry.get("error"))
        if failure_class not in {"quota_exhausted", "rate_limited", "model_unavailable", "timeout", "runtime_failure"}:
            return False
        attempts = list(entry.get("attempts") or [])
        if not attempts:
            attempts.append({
                "attempt": 1,
                "worker_id": entry.get("agent_id"),
                "task_id": entry.get("task_id"),
                "artifact_relative": entry.get("artifact_relative"),
                "status": entry.get("status"),
                "failure_class": failure_class,
                "error": entry.get("error"),
                "legacy_seeded": True,
            })
        tried = {str(item.get("worker_id")) for item in attempts if item.get("worker_id")}
        tried.add(str(entry.get("agent_id")))
        change_worker = entry.get("change_worker") or entry["implementation_worker"]
        try:
            reviewer = self._select_validation_worker(
                run,
                change_worker,
                exclude=tried,
            )
        except RuntimeError:
            return False
        attempt = len(attempts) + 1
        product_workspace = Path(run["workspace"])
        report_rel = (
            Path(".ai-team-runs") / run["run_id"] / "validation" /
            f"attempt-{attempt}-{reviewer}" / VALIDATION_REPORT
        )
        (product_workspace / report_rel).parent.mkdir(parents=True, exist_ok=True)
        failed_worker = entry.get("agent_id")
        failed_task_id = entry.get("task_id")
        prior_validation = (
            Path(entry.get("workspace") or run.get("workspace") or ".")
            / str(entry.get("artifact_relative") or VALIDATION_REPORT)
        )
        handoff = prepare_handoff(
            run=run, phase="validation", role_id="independent_validator",
            role_owner="validation", old_entry=entry,
            from_worker=str(failed_worker), to_worker=reviewer,
            to_workspace=Path(run["run_root"]) / "handoffs" / "recipients"
                         / "validation" / reviewer,
            reason=failure_class, attempt=attempt,
            previous_artifact=(
                str(prior_validation)
                if self._artifact_ready_path(prior_validation) else None
            ),
        )
        task = self.bridge.submit(
            agent_id=reviewer,
            title=f"[{run['run_id']}] Validation fallback via {reviewer}",
            prompt=self._validation_prompt(run, change_report_path, report_rel)
                   + "\n\n" + handoff["prompt_excerpt"],
            workspace=str(product_workspace),
            metadata={
                "team_run_id": run["run_id"],
                "team_phase": "validation",
                "implementation_worker": change_worker,
                "change_worker": change_worker,
                "change_report": str(change_report_path),
                "repair_attempt": entry.get("repair_attempt"),
                "validation_attempt": attempt,
                "fallback_from_worker": failed_worker,
                "fallback_from_task": failed_task_id,
                "fallback_reason": failure_class,
                "handoff_manifest": handoff["manifest"],
                "handoff_document": handoff["document"],
                "finalization_context": self._validation_finalization_context(run, change_report_path),
                "execution_budget": self._execution_budget(run, "validation"),
                "expected_artifacts": [str(report_rel)],
            },
        )
        handoff["record"].update({
            "new_task_id": task["task_id"],
            "status": "assigned",
            "manifest": handoff["manifest"],
        })
        save_handoff_state(handoff["record"])
        run.setdefault("handoffs", []).append(handoff["record"])
        if attempts:
            attempts[-1]["status"] = entry.get("status")
            attempts[-1]["failure_class"] = failure_class
            attempts[-1]["error"] = entry.get("error")
        attempts.append({
            "attempt": attempt,
            "worker_id": reviewer,
            "task_id": task["task_id"],
            "artifact_relative": str(report_rel),
            "status": task["status"],
            "failure_class": None,
            "fallback_from_worker": failed_worker,
            "fallback_from_task": failed_task_id,
            "fallback_reason": failure_class,
        })
        entry.update({
            "agent_id": reviewer,
            "task_id": task["task_id"],
            "artifact_relative": str(report_rel),
            "status": task["status"],
            "started": bool(task.get("started")),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "error": task.get("error"),
            "failure_class": None,
            "artifact": None,
            "verdict": None,
            "reassignment_count": int(entry.get("reassignment_count", 0)) + 1,
            "attempts": attempts,
            "fallback_from_worker": failed_worker,
            "fallback_from_task": failed_task_id,
            "fallback_reason": failure_class,
        })
        run.setdefault("validation_reassignments", []).append({
            "failed_worker": failed_worker,
            "failed_task_id": failed_task_id,
            "failure_class": failure_class,
            "fallback_worker": reviewer,
            "fallback_task_id": task["task_id"],
            "attempt": attempt,
            "at": _utcnow(),
        })
        run.setdefault("warnings", []).append(
            f"reassigned independent validation from {failed_worker} to {reviewer} after {failure_class}"
        )
        run["status"] = "validation_running"
        run["phase"] = "validation"
        run["phase_started_at"] = _utcnow()
        return True

    @staticmethod
    def _repair_status(path: Path) -> str | None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        for line in lines:
            value = line.strip().casefold()
            if value.startswith("repair_status:"):
                status = value.split(":", 1)[1].strip()
                return status if status in {"changed", "no_change", "blocked"} else None
        return None

    @staticmethod
    def _archive_validation(run: dict[str, Any]) -> None:
        entry = run.get("validation")
        if not entry:
            return
        # JSON round-trip 生成不可变快照，避免后续复验修改旧验证记录。
        snapshot = json.loads(json.dumps(entry))
        snapshot["archived_at"] = _utcnow()
        run.setdefault("validation_history", []).append(snapshot)
        run["validation"] = None

    def _select_repair_worker(
        self,
        run: dict[str, Any],
        *,
        triggering_validator: str,
    ) -> str:
        eligible = set(self._eligible_workers(run))
        tried_repairs = {
            str(item.get("agent_id"))
            for item in (run.get("repairs") or [])
            if item.get("agent_id")
        }
        for worker_id in IMPLEMENTATION_PREFERENCE:
            if (
                worker_id in eligible
                and worker_id != triggering_validator
                and worker_id not in tried_repairs
            ):
                return worker_id
        # 如果第二轮没有全新的修复者，允许复用健康修复者，但仍禁止 Validator 自己修自己。
        for worker_id in IMPLEMENTATION_PREFERENCE:
            if worker_id in eligible and worker_id != triggering_validator:
                return worker_id
        raise RuntimeError("no healthy repair worker available")

    def _repair_prompt(
        self,
        run: dict[str, Any],
        *,
        validation_path: Path,
        previous_change_report: Path,
        report_rel: Path,
        attempt: int,
    ) -> str:
        try:
            validation_text = validation_path.read_text(encoding="utf-8")[:20000]
        except Exception as exc:
            validation_text = f"VALIDATION REPORT READ ERROR: {exc}"
        return f"""You are the bounded repair owner for an AI product-team run.

Shared goal:
{run['goal']}

Repair attempt:
{attempt} / {run.get('policy', {}).get('max_repair_attempts', 2)}

Previous change report:
{previous_change_report}

Independent validation report that triggered this repair:
{validation_path}

Exact original acceptance checks:
{json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}

Validation evidence and required repairs:
--- BEGIN VALIDATION REPORT ---
{validation_text}
--- END VALIDATION REPORT ---

Repair rules:
- Read the validation report first. Repair only the FAILED_CHECKS / REQUIRED_REPAIR items needed to satisfy the original acceptance contract.
- Do not reopen broad product design or unrelated refactors.
- Work in the real product workspace, preserve unrelated dirty changes, and never commit or push.
- Run only the narrow tests needed to prove the repair plus practical regression tests.
- Record exact changed files, exact commands/results, unresolved evidence, and whether the repair was blocked.
- Write {report_rel}. That report is mandatory.

The report must contain:
REPAIR_STATUS: changed | no_change | blocked
## FAILED_CHECKS_ADDRESSED
## CHANGED_FILES
## ACCEPTANCE_EVIDENCE
## TEST_RESULTS
## UNRESOLVED
"""

    def _start_repair(self, run: dict[str, Any], validation_path: Path) -> dict[str, Any]:
        current_validation = run.get("validation") or {}
        validation_context = current_validation
        if not validation_context and run.get("validation_history"):
            validation_context = run["validation_history"][-1]
        max_attempts = int(run.get("policy", {}).get("max_repair_attempts", 2))
        attempt = len(run.get("repairs") or []) + 1
        if attempt > max_attempts:
            run["status"] = "failed"
            run["phase"] = "failed"
            run.setdefault("errors", []).append(
                f"repair attempts exhausted after {max_attempts} attempt(s)"
            )
            return run

        triggering_validator = str(validation_context.get("agent_id") or "")
        if run.get("repairs"):
            previous_change_report = Path(
                run["repairs"][-1].get("artifact")
                or run["repairs"][-1].get("previous_change_report")
                or ""
            )
        else:
            previous_change_report = Path(
                validation_context.get("change_report")
                or run.get("implementation", {}).get("artifact")
                or ""
            )
        try:
            worker_id = self._select_repair_worker(
                run,
                triggering_validator=triggering_validator,
            )
        except RuntimeError as exc:
            run["status"] = "blocked"
            run["phase"] = "blocked"
            run.setdefault("errors", []).append(str(exc))
            return run

        product_workspace = Path(run["workspace"])
        report_rel = (
            Path(".ai-team-runs") / run["run_id"] / "repair" /
            f"attempt-{attempt}-{worker_id}" / REPAIR_REPORT
        )
        (product_workspace / report_rel).parent.mkdir(parents=True, exist_ok=True)
        prompt = self._repair_prompt(
            run,
            validation_path=validation_path,
            previous_change_report=previous_change_report,
            report_rel=report_rel,
            attempt=attempt,
        )
        finalization_context = (
            f"Repair attempt: {attempt}/{max_attempts}\n"
            f"Triggering validation report: {validation_path}\n"
            f"Previous change report: {previous_change_report}\n"
            f"Acceptance checks: {json.dumps(run.get('product_contract', {}).get('acceptance') or [], ensure_ascii=False)}\n"
            "Required repair artifact structure: REPAIR_STATUS: changed | no_change | blocked; "
            "## FAILED_CHECKS_ADDRESSED; ## CHANGED_FILES; ## ACCEPTANCE_EVIDENCE; "
            "## TEST_RESULTS; ## UNRESOLVED."
        )
        task = self.bridge.submit(
            agent_id=worker_id,
            title=f"[{run['run_id']}] Repair attempt {attempt}",
            prompt=prompt,
            workspace=str(product_workspace),
            metadata={
                "team_run_id": run["run_id"],
                "team_phase": "repair",
                "repair_attempt": attempt,
                "triggering_validator": triggering_validator,
                "triggering_validation": str(validation_path),
                "previous_change_report": str(previous_change_report),
                "acceptance": run.get("product_contract", {}).get("acceptance") or [],
                "finalization_context": finalization_context,
                "execution_budget": self._execution_budget(run, "repair"),
                "expected_artifacts": [str(report_rel)],
            },
        )
        # 先归档触发 Repair（修复）的验证，再清空 current validation，进度才能显示“待复验”。
        self._archive_validation(run)
        run["repair"] = {
            "attempt": attempt,
            "agent_id": worker_id,
            "triggering_validator": triggering_validator,
            "triggering_validation": str(validation_path),
            "previous_change_report": str(previous_change_report),
            "task_id": task["task_id"],
            "workspace": str(product_workspace),
            "artifact_relative": str(report_rel),
            "status": task["status"],
            "started": bool(task.get("started")),
            "artifact": None,
        }
        run["status"] = "repair_running"
        run["phase"] = "repair"
        run["phase_started_at"] = _utcnow()
        return run

    @staticmethod
    def _archive_repair(run: dict[str, Any]) -> dict[str, Any] | None:
        entry = run.get("repair")
        if not entry:
            return None
        snapshot = json.loads(json.dumps(entry))
        snapshot["archived_at"] = _utcnow()
        run.setdefault("repairs", []).append(snapshot)
        run["repair"] = None
        return snapshot

    @staticmethod
    def _resolve_validation_errors(run: dict[str, Any]) -> None:
        prefixes = (
            "validation hard timeout reached",
            "implementation validation report missing",
            "implementation validation verdict:",
            "expected validation artifact missing:",
            "no healthy repair worker available",
        )
        current = list(run.get("errors") or [])
        resolved = [item for item in current if str(item).startswith(prefixes)]
        if not resolved:
            return
        run["errors"] = [item for item in current if item not in resolved]
        run.setdefault("resolved_errors", []).append({
            "phase": "validation",
            "resolved_at": _utcnow(),
            "resolution": "later independent validation accepted",
            "items": resolved,
        })

    @staticmethod
    def _validation_verdict(path: Path) -> str | None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        for line in lines:
            # 中文说明：Validator 常用 Markdown 粗体包裹整行结论，例如
            # **VERDICT: accept**。这里只接受“整行显式 VERDICT + 允许值”，
            # 不接受 "my verdict: accept" 这类叙述性文本。
            match = VALIDATION_VERDICT_RE.fullmatch(line.strip())
            if match:
                verdict = match.group(1).casefold()
                # 中文说明：Validator 生态里 PASS 与 accept 语义等价；内部统一成 accept，
                # 但仍要求整行显式 VERDICT，避免宽松文本误判。
                return "accept" if verdict == "pass" else verdict
        return None

    @staticmethod
    def _validation_required_repairs(path: Path) -> list[str]:
        """读取验证报告中的可执行修复项；`none` 一类占位文字不算真正修复任务。"""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        in_section = False
        repairs: list[str] = []
        for raw in lines:
            stripped = raw.strip()
            folded = stripped.casefold()
            if folded == "## required_repair":
                in_section = True
                continue
            if not in_section:
                continue
            if stripped.startswith("## ") or VALIDATION_VERDICT_RE.fullmatch(stripped):
                break
            item = stripped.lstrip("-*0123456789. )").strip()
            if not item:
                continue
            normalized = item.casefold()
            # 中文说明：`none until environment returns` 表示外部阻塞，不应伪装成可执行 Repair。
            if normalized.startswith(("none", "n/a", "not applicable", "no repair")):
                continue
            repairs.append(item)
        return repairs

    @classmethod
    def _effective_validation_verdict(cls, path: Path) -> tuple[str | None, str | None]:
        """同时保留验证者原始结论，并把“可修复 blocked”归一成 repair。"""
        reported = cls._validation_verdict(path)
        effective = reported
        # 中文说明：代码/测试可修的问题不能用 blocked 逃出自动修复循环。
        # 真正 blocked 应用于当前工作区内无法解决的资源、权限或外部依赖问题。
        if reported == "blocked" and cls._validation_required_repairs(path):
            effective = "repair"
        return reported, effective

    def _apply_phase_deadline(
        self,
        run: dict[str, Any],
        *,
        phase: str,
        entries: list[dict[str, Any]],
    ) -> None:
        policy = run.get("policy", {})
        soft = float(policy.get(f"{phase}_soft_timeout_seconds", 90))
        default_hard = 420 if phase == "primary" else 300
        hard = float(policy.get(f"{phase}_hard_timeout_seconds", default_hard))
        age = _age_seconds(run.get("phase_started_at") or run.get("created_at"))
        active = [entry for entry in entries if entry.get("status") not in TERMINAL]
        if not active:
            return
        cycle = str(run.get("phase_started_at") or run.get("created_at") or "")
        filename = {"primary": "primary.md", "review": "review.md", "synthesis": "final.md"}.get(phase)
        ready_nudges = run.setdefault("artifact_ready_nudges", {})
        for entry in active:
            # Implementation/Validation/Repair（实现/验证/修复）产物位于产品工作区的嵌套路径。
            entry_filename = filename or (
                entry.get("artifact_relative")
                if phase in {"implementation", "validation", "repair"}
                else None
            )
            ready_key = f"{phase}:{cycle}:{entry.get('task_id')}"
            if entry_filename and ready_key not in ready_nudges and self._artifact_ready(entry, entry_filename):
                source = Path(entry["workspace"]) / entry_filename
                snapshot_dir = Path(run["run_root"]) / "ready-artifact-snapshots"
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                snapshot_path = snapshot_dir / (
                    f"{phase}-{entry.get('task_id')}-{Path(entry_filename).name}"
                )
                try:
                    # 中文说明：收尾会话可能覆盖已经 decision-ready（可决策）的成果。
                    # 先保存快照，终态文件若被写坏仍可恢复真实 ready 证据。
                    shutil.copy2(source, snapshot_path)
                    entry["ready_artifact_snapshot"] = str(snapshot_path)
                    run.setdefault("ready_artifact_snapshots", {})[ready_key] = {
                        "phase": phase,
                        "task_id": entry.get("task_id"),
                        "source": str(source),
                        "snapshot": str(snapshot_path),
                        "captured_at": _utcnow(),
                    }
                except Exception as exc:
                    run.setdefault("warnings", []).append(
                        f"ready-artifact snapshot failed for {entry.get('task_id')}: {exc}"
                    )
                try:
                    self.bridge.message(
                        entry["task_id"],
                        "The mandatory artifact is already present and decision-ready. Stop all new exploration now, "
                        "perform only the minimum final consistency check, and end the task immediately.",
                    )
                    ready_nudges[ready_key] = _utcnow()
                    run.setdefault("warnings", []).append(
                        f"{phase} artifact ready while task still active; requested immediate finalization for {entry['task_id']}"
                    )
                except Exception as exc:
                    run.setdefault("warnings", []).append(
                        f"artifact-ready finalization message failed for {entry['task_id']}: {exc}"
                    )
        nudges = run.setdefault("nudges", {})
        prior_nudge = nudges.get(phase) or {}
        if age >= soft and prior_nudge.get("cycle") != cycle:
            nudge = {"at": _utcnow(), "cycle": cycle, "task_ids": []}
            for entry in active:
                try:
                    self.bridge.message(
                        entry["task_id"],
                        "Supervisor soft deadline reached. Stop starting new exploration. "
                        "Converge now from the evidence already gathered, validate what you can, "
                        "and emit the mandatory artifact before doing anything optional.",
                    )
                    nudge["task_ids"].append(entry["task_id"])
                except Exception as exc:
                    run.setdefault("warnings", []).append(
                        f"soft-timeout convergence message failed for {entry['task_id']}: {exc}"
                    )
            nudges[phase] = nudge
            run.setdefault("warnings", []).append(
                f"{phase} soft timeout observed after {int(age)}s; "
                f"force-convergence requested for {len(nudge['task_ids'])} active task(s)"
            )
        hard_deadlines = run.setdefault("hard_deadlines", {})
        if age >= hard and hard_deadlines.get(phase) != cycle:
            hard_deadlines[phase] = cycle
            for entry in active:
                try:
                    self.bridge.cancel(entry["task_id"])
                except Exception as exc:
                    run.setdefault("errors", []).append(
                        f"hard-timeout cancellation failed for {entry['task_id']}: {exc}"
                    )
            run.setdefault("errors", []).append(
                f"{phase} hard timeout reached after {int(age)}s; continuing in degraded mode"
            )
            for entry in active:
                self._refresh_entry(entry)
                entry["failure_class"] = "timeout"
                attempts = entry.get("attempts") or []
                if attempts and attempts[-1].get("task_id") == entry.get("task_id"):
                    attempts[-1]["failure_class"] = "timeout"
                entry_filename = filename or (
                    entry.get("artifact_relative")
                    if phase in {"implementation", "validation", "repair"}
                    else None
                )
                if entry_filename and self._salvage_entry(entry, entry_filename):
                    run.setdefault("warnings", []).append(
                        f"salvaged {entry_filename} from timed-out task {entry['task_id']}"
                    )

    def _recover_retryable_repair_block(self, run: dict[str, Any]) -> dict[str, Any]:
        """把已终止但仍有修复次数余额的 blocked repair 恢复到下一轮有界修复。"""
        if run.get("status") != "blocked" or run.get("phase") != "blocked":
            return run
        repairs = run.get("repairs") or []
        if not repairs:
            return run
        latest = repairs[-1]
        if latest.get("repair_status") != "blocked":
            return run
        max_repairs = int(run.get("policy", {}).get("max_repair_attempts", 2))
        if len(repairs) >= max_repairs:
            return run
        validation_path = Path(str(latest.get("triggering_validation") or ""))
        if not validation_path.is_file():
            return run

        # 中文说明：第一轮修复失败不等于整个项目无法继续，只要还有修复预算就继续下一轮。
        message = f"repair attempt {latest.get('attempt')} reported blocked"
        current_errors = list(run.get("errors") or [])
        if message in current_errors:
            run["errors"] = [item for item in current_errors if item != message]
            run.setdefault("resolved_errors", []).append({
                "phase": "repair",
                "resolved_at": _utcnow(),
                "resolution": "terminal blocked repair resumed within remaining repair budget",
                "items": [message],
            })
        warning = f"repair attempt {latest.get('attempt')} blocked; resuming bounded retry"
        if warning not in run.setdefault("warnings", []):
            run["warnings"].append(warning)
        return self._start_repair(run, validation_path)

    def _recover_repairable_validation_block(self, run: dict[str, Any]) -> dict[str, Any]:
        """把历史上误判为 blocked、但已有明确修复项的验证恢复到 Repair Loop。"""
        if run.get("status") != "blocked" or run.get("phase") != "blocked":
            return run
        validation = run.get("validation") or {}
        artifact = validation.get("artifact")
        if not artifact:
            relative = validation.get("artifact_relative")
            workspace = validation.get("workspace") or run.get("workspace")
            if relative and workspace:
                candidate = Path(workspace) / str(relative)
                if candidate.is_file():
                    artifact = str(candidate)
        if not artifact:
            return run
        path = Path(artifact)
        reported, effective = self._effective_validation_verdict(path)
        if reported != "blocked" or effective != "repair":
            return run

        # 中文说明：不删除历史事实，而是把错误终止原因移入 resolved_errors，再继续修复。
        blocked_error = "implementation validation verdict: blocked"
        current_errors = list(run.get("errors") or [])
        if blocked_error in current_errors:
            run["errors"] = [item for item in current_errors if item != blocked_error]
            run.setdefault("resolved_errors", []).append({
                "phase": "validation",
                "resolved_at": _utcnow(),
                "resolution": "repairable blocked verdict normalized to repair",
                "items": [blocked_error],
            })
        validation["reported_verdict"] = reported
        validation["verdict"] = effective
        validation["artifact"] = str(path)
        run["implementation_validation_reported_verdict"] = reported
        run["implementation_validation_verdict"] = effective
        warning = (
            "recovered blocked validation with actionable REQUIRED_REPAIR; "
            "resuming automatic repair loop"
        )
        if warning not in run.setdefault("warnings", []):
            run["warnings"].append(warning)
        if "implementation validation verdict: repair" not in run.setdefault("errors", []):
            run["errors"].append("implementation validation verdict: repair")
        return self._start_repair(run, path)

    def _recover_missing_independent_validator(self, run: dict[str, Any]) -> dict[str, Any]:
        """独立 Validator 恢复可用后，从 blocked validation gate 原地续跑。"""
        if run.get("status") != "blocked" or run.get("phase") != "blocked":
            return run
        coverage = run.get("coverage_gaps") or {}
        if not coverage.get("missing_independent_validator"):
            return run

        implementation = run.get("implementation") or {}
        if not implementation:
            return run
        change_worker = implementation.get("agent_id")
        if not change_worker:
            return run

        report_path = (
            Path(implementation.get("workspace") or run.get("workspace") or ".")
            / str(implementation.get("artifact_relative") or IMPLEMENTATION_REPORT)
        )
        if not self._artifact_ready_path(report_path):
            return run

        try:
            # 中文说明：这里先只探测“现在是否已有独立且健康的验证者”。
            # 真正创建 validation task 仍统一交给 _start_validation，避免重复选择逻辑。
            self._select_validation_worker(run, change_worker)
        except RuntimeError:
            return run

        blocked_error = "no healthy independent worker available for implementation validation"
        current_errors = list(run.get("errors") or [])
        if blocked_error in current_errors:
            run["errors"] = [item for item in current_errors if item != blocked_error]
            run.setdefault("resolved_errors", []).append({
                "phase": "validation",
                "resolved_at": _utcnow(),
                "resolution": "independent validator became available; blocked validation gate resumed",
                "items": [blocked_error],
            })

        coverage.pop("missing_independent_validator", None)
        if coverage:
            run["coverage_gaps"] = coverage
        else:
            run.pop("coverage_gaps", None)

        warning = "independent validator became available; resuming blocked validation gate"
        if warning not in run.setdefault("warnings", []):
            run["warnings"].append(warning)
        return self._start_validation(
            run,
            report_path,
            change_worker=change_worker,
        )

    def _recover_invalid_validation_artifact(self, run: dict[str, Any]) -> dict[str, Any]:
        """Supervisor/Worker 收尾损坏验收文件时，只重跑一次独立 Validation。"""
        if run.get("status") != "failed" or run.get("phase") != "failed":
            return run
        parser_error = "implementation validation report missing a valid VERDICT"
        if parser_error not in (run.get("errors") or []):
            return run
        if int(run.get("validation_artifact_retries", 0)) >= 1:
            return run
        validation = run.get("validation") or {}
        change_report = validation.get("change_report")
        change_worker = validation.get("change_worker") or validation.get("implementation_worker")
        if not change_report or not Path(str(change_report)).is_file() or not change_worker:
            return run
        retry_validator = str(validation.get("agent_id") or "")
        if not retry_validator or retry_validator == str(change_worker):
            return run
        by_id = {item["agent_id"]: item for item in self.bridge.agents()}
        retry_state = by_id.get(retry_validator) or {}
        if not (
            retry_state.get("configured")
            and retry_state.get("available")
            and retry_state.get("queueable")
        ):
            return run

        old_report = (
            Path(validation.get("workspace") or run.get("workspace") or ".")
            / str(validation.get("artifact_relative") or VALIDATION_REPORT)
        )
        if old_report.is_file():
            archive_dir = Path(run["run_root"]) / "validation" / "invalid-artifacts"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / "attempt-1-invalid-validation-report.md"
            shutil.copy2(old_report, archive_path)
            validation["invalid_artifact_archive"] = str(archive_path)
        self._archive_validation(run)

        run["validation_artifact_retries"] = int(run.get("validation_artifact_retries", 0)) + 1
        run["errors"] = [item for item in (run.get("errors") or []) if item != parser_error]
        run.setdefault("resolved_errors", []).append({
            "phase": "validation",
            "resolved_at": _utcnow(),
            "resolution": "invalid finalization artifact archived; independent validation retried once",
            "items": [parser_error],
        })
        warning = (
            "validation artifact lost its contract verdict during finalization; "
            "archived invalid artifact and retrying independent validation once"
        )
        if warning not in run.setdefault("warnings", []):
            run["warnings"].append(warning)
        return self._start_validation(
            run,
            Path(str(change_report)),
            change_worker=str(change_worker),
            reviewer_override=retry_validator,
        )

    def _recover_late_terminal_artifacts(self, run: dict[str, Any]) -> dict[str, Any]:
        if run.get("status") != "failed" or run.get("phase") != "failed":
            return run

        # 兼容旧 Supervisor：Implementation 已产出，但启动独立验证时因无健康 Validator 抛异常，
        # advance_all 把顶层 run 写成 failed，同时丢失了刚刷新的 implementation terminal 状态。
        # 这里仅在证据确实存在时纠正为 blocked；不会把毕业/验收结果改写为成功。
        if any(
            "no healthy independent worker available for implementation validation" in str(item)
            for item in (run.get("errors") or [])
        ):
            implementation = run.get("implementation") or {}
            if implementation:
                self._refresh_entry(implementation)
                path = (
                    Path(implementation.get("workspace") or run.get("workspace") or ".")
                    / str(implementation.get("artifact_relative") or IMPLEMENTATION_REPORT)
                )
                if self._artifact_ready_path(path):
                    implementation["artifact"] = str(path)
                    coverage = run.setdefault("coverage_gaps", {})
                    coverage["missing_independent_validator"] = True
                    run["status"] = "blocked"
                    run["phase"] = "blocked"
                    run["degraded"] = True
                    warning = (
                        "reclassified legacy validation-start failure as blocked after recovering "
                        "a ready implementation artifact"
                    )
                    if warning not in run.setdefault("warnings", []):
                        run["warnings"].append(warning)
                    return run

        entry = run.get("synthesis") or {}
        if not entry or entry.get("status") not in TERMINAL or run.get("final_artifact"):
            return run
        if not self._salvage_entry(entry, "final.md"):
            return run
        final_path = Path(entry["salvaged_artifact"])
        run["degraded"] = True
        run["final_artifact"] = str(final_path)
        run.setdefault("warnings", []).append(
            f"late recovery salvaged final.md after terminal synthesis task {entry['task_id']}"
        )
        if run.get("policy", {}).get("implementation_enabled"):
            return self._start_implementation(run, final_path)
        run["status"] = "completed"
        run["phase"] = "completed"
        return run

    def _recover_parseable_validation_accept(self, run: dict[str, Any]) -> dict[str, Any]:
        """恢复旧解析器/旧状态把最终明确 accept 留在 failed/blocked 的运行。"""
        if run.get("status") not in {"failed", "blocked"}:
            return run
        validation = run.get("validation") or {}
        if validation.get("status") not in TERMINAL:
            return run
        path = (
            Path(validation.get("workspace") or run.get("workspace") or ".")
            / str(validation.get("artifact_relative") or VALIDATION_REPORT)
        )
        if not path.is_file():
            return run
        reported, effective = self._effective_validation_verdict(path)
        if reported != "accept" or effective != "accept":
            return run

        # 中文说明：不删除旧解析器造成的失败历史；把它移入 resolved_errors，
        # 并复用同一份已经存在的独立验收证据恢复完成状态。
        validation["artifact"] = str(path)
        validation["reported_verdict"] = reported
        validation["verdict"] = effective
        run["implementation_validation_reported_verdict"] = reported
        run["implementation_validation_verdict"] = effective
        self._resolve_validation_errors(run)
        # 如果还有与验证无关的错误，保留 blocked/failed，不把其他真实失败吞掉。
        if run.get("errors"):
            return run
        warning = "recovered final accepted validation after verdict/state normalization"
        if warning not in run.setdefault("warnings", []):
            run["warnings"].append(warning)
        run["status"] = "completed"
        run["phase"] = "completed"
        return run

    def advance(self, run_id: str) -> dict[str, Any]:
        run = self.runs.load(run_id)
        if run.get("status") not in ACTIVE_RUN_STATUSES:
            before = (run.get("status"), run.get("phase"), run.get("final_artifact"))
            recovered = self._recover_retryable_repair_block(run)
            if recovered.get("status") not in ACTIVE_RUN_STATUSES:
                recovered = self._recover_repairable_validation_block(recovered)
            if recovered.get("status") not in ACTIVE_RUN_STATUSES:
                recovered = self._recover_missing_independent_validator(recovered)
            if recovered.get("status") not in ACTIVE_RUN_STATUSES:
                recovered = self._recover_parseable_validation_accept(recovered)
            if recovered.get("status") not in ACTIVE_RUN_STATUSES:
                recovered = self._recover_invalid_validation_artifact(recovered)
            if recovered.get("status") not in ACTIVE_RUN_STATUSES:
                recovered = self._recover_late_terminal_artifacts(recovered)
            after = (recovered.get("status"), recovered.get("phase"), recovered.get("final_artifact"))
            self._attach_progress(recovered)
            self.runs.save(recovered)
            return recovered
        run = self._refresh_task_views(run)
        if run["status"] == "primary_running":
            # 未启动任务对应 Worker 持续不可用时，提前取消并立即进入健康感知批量重分工。
            unavailable_cancelled = self._cancel_unavailable_queued_primary(run)
            if unavailable_cancelled:
                self._reassign_failed_primary_roles(run)
            entries = list(run["primary"].values())
            self._apply_phase_deadline(run, phase="primary", entries=entries)
            if self._all_terminal(entries):
                if not self._reassign_failed_primary_roles(run):
                    run = self._start_reviews(run)
        elif run["status"] == "review_running":
            entries = list(run["reviews"].values())
            if not entries:
                # 兼容旧状态：历史 run 可能已经进入空 review_running。
                # 不能永久等待一个永远不会出现的 Reviewer，必须显式降级并继续。
                coverage = run.setdefault("coverage_gaps", {})
                coverage["missing_review_count"] = int(
                    run.get("policy", {}).get(
                        "min_review_success",
                        min(2, len(run["participants"])),
                    )
                )
                run["degraded"] = True
                warning = "review gate degraded: no healthy independent reviewer available"
                if warning not in run.setdefault("warnings", []):
                    run["warnings"].append(warning)
                run = self._start_synthesis(run, allow_no_reviews=True)
            else:
                self._apply_phase_deadline(run, phase="review", entries=entries)
            if entries and self._all_terminal(entries):
                for entry in entries:
                    if self._salvage_entry(entry, "review.md"):
                        run.setdefault("warnings", []).append(
                            f"salvaged review.md from terminal task {entry['task_id']}"
                        )
                run = self._start_synthesis(run)
        elif run["status"] == "synthesis_running":
            entry = run.get("synthesis") or {}
            self._apply_phase_deadline(run, phase="synthesis", entries=[entry])
            if entry.get("status") in TERMINAL:
                if entry.get("status") == "completed":
                    try:
                        final_path = self._resolve_artifact(entry, "final.md")
                    except FileNotFoundError as exc:
                        run["status"] = "failed"
                        run["phase"] = "failed"
                        run["errors"].append(str(exc))
                    else:
                        entry["artifact"] = str(final_path)
                        run["final_artifact"] = str(final_path)
                        if run.get("policy", {}).get("implementation_enabled"):
                            run = self._start_implementation(run, final_path)
                        else:
                            run["status"] = "completed"
                            run["phase"] = "completed"
                else:
                    if self._salvage_entry(entry, "final.md"):
                        final_path = Path(entry["salvaged_artifact"])
                        run["degraded"] = True
                        run.setdefault("warnings", []).append(
                            f"salvaged final.md from terminal synthesis task {entry['task_id']}"
                        )
                        run["final_artifact"] = str(final_path)
                        if run.get("policy", {}).get("implementation_enabled"):
                            run = self._start_implementation(run, final_path)
                        else:
                            run["status"] = "completed"
                            run["phase"] = "completed"
                    else:
                        run["status"] = "failed"
                        run["phase"] = "failed"
                        run["errors"].append(
                            f"synthesis task ended with {entry.get('status')}: "
                            f"{entry.get('error') or ''}".strip()
                        )
        elif run["status"] == "implementation_running":
            entry = run.get("implementation") or {}
            self._apply_phase_deadline(run, phase="implementation", entries=[entry])
            if entry.get("status") in TERMINAL:
                try:
                    implementation_path = self._implementation_artifact(entry)
                except FileNotFoundError as exc:
                    run["status"] = "failed"
                    run["phase"] = "failed"
                    run["errors"].append(str(exc))
                else:
                    entry["artifact"] = str(implementation_path)
                    if entry.get("status") != "completed":
                        run["degraded"] = True
                        run.setdefault("warnings", []).append(
                            f"salvaged implementation report from terminal task {entry['task_id']}"
                        )
                    missing_deliverables = self._missing_product_deliverables(run)
                    if missing_deliverables:
                        run["status"] = "failed"
                        run["phase"] = "failed"
                        run.setdefault("errors", []).append(
                            "mandatory product deliverable missing or incomplete: "
                            + ", ".join(missing_deliverables)
                        )
                    else:
                        run = self._start_validation(run, implementation_path)
        elif run["status"] == "repair_running":
            entry = run.get("repair") or {}
            self._apply_phase_deadline(run, phase="repair", entries=[entry])
            if entry.get("status") in TERMINAL:
                path = Path(entry["workspace"]) / entry.get("artifact_relative", REPAIR_REPORT)
                if not self._artifact_ready_path(path):
                    # 中文说明：Repair Worker（修复执行器）进程失败不等于产品不可修复。
                    # 先归档这次失败，保留 process truth（进程真相），再在剩余预算内换健康 Worker 重试。
                    archived = self._archive_repair(run) or {}
                    message = f"expected repair artifact missing or incomplete: {path}"
                    run.setdefault("errors", []).append(message)
                    max_repairs = int(run.get("policy", {}).get("max_repair_attempts", 2))
                    validation_path = Path(
                        archived.get("triggering_validation")
                        or (run.get("validation_history") or [{}])[-1].get("artifact")
                        or ""
                    )
                    if len(run.get("repairs") or []) < max_repairs and validation_path.is_file():
                        run.setdefault("resolved_errors", []).append({
                            "phase": "repair",
                            "resolved_at": _utcnow(),
                            "resolution": "failed repair worker consumed one bounded attempt; retrying with another healthy worker",
                            "items": [message],
                        })
                        run["errors"] = [item for item in run.get("errors", []) if item != message]
                        run.setdefault("warnings", []).append(
                            f"repair attempt {archived.get('attempt')} failed without ready artifact; starting bounded fallback"
                        )
                        run = self._start_repair(run, validation_path)
                    else:
                        run["status"] = "failed"
                        run["phase"] = "failed"
                else:
                    repair_status = self._repair_status(path)
                    entry["artifact"] = str(path)
                    entry["repair_status"] = repair_status
                    if entry.get("status") != "completed":
                        run["degraded"] = True
                        run.setdefault("warnings", []).append(
                            f"accepted repair artifact from terminal {entry.get('status')} task {entry['task_id']}"
                        )
                    repair_worker = str(entry.get("agent_id") or "")
                    repair_attempt = int(entry.get("attempt") or 0)
                    archived = self._archive_repair(run) or {}
                    if repair_status == "blocked":
                        max_repairs = int(run.get("policy", {}).get("max_repair_attempts", 2))
                        message = f"repair attempt {repair_attempt} reported blocked"
                        run.setdefault("errors", []).append(message)
                        if len(run.get("repairs") or []) < max_repairs:
                            validation_path = Path(
                                archived.get("triggering_validation")
                                or (run.get("validation_history") or [{}])[-1].get("artifact")
                                or ""
                            )
                            if validation_path.is_file():
                                run.setdefault("resolved_errors", []).append({
                                    "phase": "repair",
                                    "resolved_at": _utcnow(),
                                    "resolution": "blocked repair consumed one bounded attempt; retrying with another healthy worker",
                                    "items": [message],
                                })
                                run["errors"] = [item for item in run.get("errors", []) if item != message]
                                run.setdefault("warnings", []).append(
                                    f"repair attempt {repair_attempt} blocked; starting bounded retry"
                                )
                                run = self._start_repair(run, validation_path)
                            else:
                                run["status"] = "blocked"
                                run["phase"] = "blocked"
                        else:
                            run["status"] = "blocked"
                            run["phase"] = "blocked"
                    elif repair_status in {"changed", "no_change"}:
                        run = self._start_validation(
                            run,
                            path,
                            change_worker=repair_worker,
                            repair_attempt=repair_attempt,
                        )
                    else:
                        run["status"] = "failed"
                        run["phase"] = "failed"
                        run.setdefault("errors", []).append(
                            f"repair report missing valid REPAIR_STATUS: {archived.get('artifact_relative') or path}"
                        )
        elif run["status"] == "validation_running":
            entry = run.get("validation") or {}
            self._apply_phase_deadline(run, phase="validation", entries=[entry])
            if entry.get("status") in TERMINAL:
                path = Path(entry["workspace"]) / entry.get("artifact_relative", VALIDATION_REPORT)
                if path.is_file():
                    reported_verdict, verdict = self._effective_validation_verdict(path)
                else:
                    reported_verdict, verdict = None, None
                if verdict is None:
                    snapshot = entry.get("ready_artifact_snapshot")
                    snapshot_path = Path(str(snapshot)) if snapshot else None
                    if snapshot_path and snapshot_path.is_file():
                        snapshot_reported, snapshot_verdict = self._effective_validation_verdict(
                            snapshot_path
                        )
                        if snapshot_verdict is not None:
                            path = snapshot_path
                            reported_verdict = snapshot_reported
                            verdict = snapshot_verdict
                            warning = (
                                "validation terminal artifact lost a valid verdict; "
                                "recovered the previously captured ready-artifact snapshot"
                            )
                            if warning not in run.setdefault("warnings", []):
                                run["warnings"].append(warning)
                if verdict is None and entry.get("status") in {"failed", "cancelled"}:
                    change_report_path = Path(
                        entry.get("change_report")
                        or run["implementation"]["artifact"]
                    )
                    if self._reassign_validation(run, change_report_path):
                        self._attach_progress(run)
                        self.runs.save(run)
                        return run
                if not path.is_file():
                    run["status"] = "failed"
                    run["phase"] = "failed"
                    run["errors"].append(f"expected validation artifact missing: {path}")
                else:
                    entry["artifact"] = str(path)
                    entry["reported_verdict"] = reported_verdict
                    entry["verdict"] = verdict
                    run["implementation_validation_reported_verdict"] = reported_verdict
                    run["implementation_validation_verdict"] = verdict
                    if reported_verdict != verdict and reported_verdict is not None:
                        warning = (
                            f"validation reported {reported_verdict} but contains actionable REQUIRED_REPAIR; "
                            f"effective verdict normalized to {verdict}"
                        )
                        if warning not in run.setdefault("warnings", []):
                            run["warnings"].append(warning)
                    if entry.get("status") != "completed" and verdict is not None:
                        run["degraded"] = True
                        run.setdefault("warnings", []).append(
                            f"accepted validation verdict from terminal {entry.get('status')} task {entry['task_id']}"
                        )
                    if verdict == "accept":
                        self._resolve_validation_errors(run)
                        run["status"] = "completed"
                        run["phase"] = "completed"
                    elif verdict == "repair":
                        run.setdefault("errors", []).append(
                            "implementation validation verdict: repair"
                        )
                        max_repairs = int(run.get("policy", {}).get("max_repair_attempts", 2))
                        if len(run.get("repairs") or []) >= max_repairs:
                            run["status"] = "failed"
                            run["phase"] = "failed"
                            run.setdefault("errors", []).append(
                                f"repair attempts exhausted after {max_repairs} attempt(s)"
                            )
                        else:
                            run = self._start_repair(run, path)
                    elif verdict == "blocked":
                        run["status"] = "blocked"
                        run["phase"] = "blocked"
                        run.setdefault("errors", []).append(
                            "implementation validation verdict: blocked"
                        )
                    else:
                        run["status"] = "failed"
                        run["phase"] = "failed"
                        run["errors"].append("implementation validation report missing a valid VERDICT")
        self._attach_progress(run)
        self.runs.save(run)
        return run

    def advance_all(self) -> list[dict[str, Any]]:
        results = []
        # 轮询自动检测成员状态变更。发现新 Worker 不代表可以抢占现有健康任务。
        roster_events: list[dict[str, Any]] = []
        try:
            roster_events = self.roster_monitor.observe(self.bridge.agents())
        except Exception:
            pass  # 监测失败不阻断已有 Supervisor 派工状态机。
        # 新成员真正上线时，主动重试“缺少独立验收者”的 blocked 关卡。
        # 复用 advance 的既有健康门禁：原 Worker 健康不抢占、仅在
        # 本轮局部健康且与实施者不同的 Validator 可用时恢复。
        if any(
            event.get("kind") in {
                "worker_online", "worker_registered",
                "worker_capabilities_changed",
            } and bool((event.get("after") or {}).get("available"))
            for event in roster_events
        ):
            for run in self.runs.list(active_only=False):
                if (run.get("status") != "blocked" or
                        not (run.get("coverage_gaps") or {}).get(
                            "missing_independent_validator"
                        )):
                    continue
                try:
                    results.append(self.advance(run["run_id"]))
                except Exception:
                    # 本轮留在 blocked 以待人工排查；绝不清空既有任务历史。
                    pass
        for run in self.runs.list(active_only=True):
            try:
                results.append(self.advance(run["run_id"]))
            except Exception as exc:
                current = self.runs.load(run["run_id"])
                current["status"] = "failed"
                current["phase"] = "failed"
                current.setdefault("errors", []).append(str(exc))
                self.runs.save(current)
                results.append(current)
        return results
