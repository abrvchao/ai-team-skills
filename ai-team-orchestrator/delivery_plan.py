"""Delivery Plan（交付计划）: goal → phases → workers → schedule → due time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


DELIVERY_PLAN_VERSION = "delivery-plan-v0.1"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def build_delivery_plan(
    *,
    goal: str,
    participants: Sequence[str],
    role_profiles: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
    created_at: str,
    implementation_enabled: bool,
) -> dict[str, Any]:
    """生成与 Supervisor 真实 hard deadline（硬截止）一致的交付日程。"""
    start = _parse_time(created_at)
    phases: list[tuple[str, str, list[str], list[str]]] = [
        ("primary", "并行研究 / 方案 / 工程判断", list(participants), []),
        ("review", "独立交叉审查", list(participants), ["primary"]),
        ("synthesis", "综合结论与决策收敛", ["best_available_synthesizer"], ["review"]),
    ]
    if implementation_enabled:
        phases.extend([
            ("implementation", "在真实工作区实现已接受决策", ["best_available_implementer"], ["synthesis"]),
            ("validation", "独立重跑验收并给出 verdict（结论）", ["independent_validator"], ["implementation"]),
        ])

    cursor = start
    rows: list[dict[str, Any]] = []
    for phase, objective, workers, dependencies in phases:
        hard_seconds = int(policy.get(f"{phase}_hard_timeout_seconds", 300))
        finish = cursor + timedelta(seconds=hard_seconds)
        rows.append({
            "phase": phase,
            "objective": objective,
            "depends_on": dependencies,
            "planned_workers": workers,
            "planned_start_at": _iso(cursor),
            "planned_finish_at": _iso(finish),
            "hard_budget_seconds": hard_seconds,
        })
        cursor = finish

    # 中文说明：Repair（修复）不是正常必经阶段，不伪造进主日程；
    # 只预留 contingency（应急缓冲），实际触发时由 Supervisor 记录并重排。
    repair_attempts = int(policy.get("max_repair_attempts", 0)) if implementation_enabled else 0
    repair_seconds = int(policy.get("repair_hard_timeout_seconds", 0)) * repair_attempts
    project_due = cursor + timedelta(seconds=repair_seconds)

    return {
        "version": DELIVERY_PLAN_VERSION,
        "goal": goal.strip(),
        "created_at": created_at,
        "schedule_basis": "supervisor_hard_deadlines",
        "participants": [
            {
                "worker_id": worker,
                "role_id": (role_profiles.get(worker) or {}).get("role_id"),
                "role_label": (role_profiles.get(worker) or {}).get("label"),
            }
            for worker in participants
        ],
        "phases": rows,
        "repair_contingency_seconds": repair_seconds,
        "project_due_at": _iso(project_due),
        "truth_rule": (
            "这是执行控制日程，不是完成时间保证；发生 Worker 故障、外部阻塞或 Repair 时必须显式重排，"
            "不能静默把延期包装成按时完成。"
        ),
    }


def schedule_state(
    plan: Mapping[str, Any],
    *,
    now: datetime | None = None,
    completed: bool = False,
) -> str:
    if completed:
        return "completed"
    due = _parse_time(str(plan.get("project_due_at") or ""))
    current = now or datetime.now(timezone.utc)
    return "late" if current > due else "on_track"
