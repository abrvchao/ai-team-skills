"""AI Team 项目 / 子任务 / Worker 的只读进度聚合。

只查询 Bridge 和 Supervisor 原始真相源；不构造人工百分比、不返回消息、
任务原始提示、模型凭据或错误原文。供本机 Dashboard 独立访问。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


ACTIVE_STATUSES = frozenset({"running", "claimed", "queued", "pending", "assigned"})
PHASES = (
    ("primary", "初稿与分工"),
    ("review", "交叉审查"),
    ("synthesis", "结果综合"),
    ("implementation", "代码实施"),
    ("repair", "修复"),
    ("validation", "独立验收"),
)


def reported_progress(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    """只呈现 Worker 最后有效上报。无数据返回 None，绝不估算百分比。"""
    if not events:
        return {"percent": None, "stage": None, "message": None, "updated_at": None}
    latest = events[-1]
    percent = None
    for event in reversed(events):
        value = event.get("percent")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100:
            percent = round(value, 1)
            break
    return {
        "percent": percent,
        "stage": str(latest.get("stage") or "")[:100] or None,
        "message": str(latest.get("message") or "")[:240] or None,
        "updated_at": latest.get("created_at"),
    }


def _task(row: Mapping[str, Any], *, phase: str, role: str,
          bridge_row: Mapping[str, Any] | None, progress: Mapping[str, Any] | None) -> dict[str, Any]:
    live = bridge_row or {}
    worker = row.get("worker_id") or row.get("agent_id") or row.get("reviewer")
    return {
        "task_id": row.get("task_id"),
        "worker_id": worker,
        "role": str(role)[:120],
        "phase": phase,
        "status": live.get("status") or row.get("status") or "unknown",
        "started": bool(live.get("started", row.get("started", False))),
        "created_at": live.get("created_at") or row.get("created_at"),
        "started_at": live.get("started_at") or row.get("started_at"),
        "finished_at": live.get("finished_at") or row.get("finished_at"),
        "artifact_registered": bool(row.get("artifact")),
        "source": "bridge" if bridge_row else "supervisor_snapshot",
        "progress": dict(progress or reported_progress([])),
    }


def _run_tasks(run: Mapping[str, Any], index: Mapping[str, dict[str, Any]],
               progress_cache: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = []

    def add(row: Any, phase: str, role: str) -> None:
        if not isinstance(row, dict) or not row.get("task_id"):
            return
        task_id = str(row["task_id"])
        items.append(_task(row, phase=phase, role=role, bridge_row=index.get(task_id),
                           progress=progress_cache.get(task_id)))

    for role, task in (run.get("primary") or {}).items():
        add(task, "primary", str(role))
    for role, task in (run.get("reviews") or {}).items():
        add(task, "review", str(role))
    for phase in ("synthesis", "implementation", "validation"):
        add(run.get(phase), phase, phase)
    for i, row in enumerate(run.get("repairs") or [], start=1):
        add(row, "repair", "repair-" + str(i))

    return items


def build_monitor(
    client: Any, run_state_dir: Path, agents: list[dict[str, Any]],
    *, limit_runs: int = 8, max_progress_reads: int = 16,
) -> dict[str, Any]:
    """失败降级后仍显示可核验的本地 Run，不把 Bridge 断线当作全员空闲。"""
    bridge_available = True
    try:
        bridge_tasks = list(client.tasks())
    except Exception:
        bridge_tasks = []
        bridge_available = False

    # Bridge /tasks 目前只提供最近一页（默认 100 条）；统计值明确标注采样范围。
    index = {str(x.get("task_id")): x for x in bridge_tasks if x.get("task_id")}
    ordered_runs = []
    if run_state_dir.is_dir():
        for path in sorted(
            run_state_dir.glob("run_*.json"), key=lambda x: x.stat().st_mtime,
            reverse=True,
        )[:max(1, min(limit_runs, 20))]:
            try:
                ordered_runs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue

    # 优先读取当前执行任务的实际进度事件，避免轮询整个历史库。
    active_ids = []
    for row in bridge_tasks:
        if row.get("status") in ACTIVE_STATUSES and row.get("task_id"):
            active_ids.append(str(row["task_id"]))
    for run in ordered_runs:
        for item in _run_tasks(run, index, {}):
            if item["status"] in ACTIVE_STATUSES and item.get("task_id"):
                active_ids.append(str(item["task_id"]))
    # 当前空闲的 Worker 也可能在上一条任务中上报了真实进度事件。
    # 每人最多再取一条最近任务，避免把已经上报的百分比误写成“无记录”。
    latest_by_worker: dict[str, str] = {}
    for row in sorted(bridge_tasks, key=lambda x: str(x.get("created_at") or ""), reverse=True):
        worker_id = str(row.get("agent_id") or "")
        task_id = str(row.get("task_id") or "")
        if worker_id and task_id and worker_id not in latest_by_worker:
            latest_by_worker[worker_id] = task_id
    active_ids.extend(latest_by_worker.values())
    cache: dict[str, dict[str, Any]] = {}
    for task_id in dict.fromkeys(active_ids):
        if len(cache) >= max_progress_reads or not re.fullmatch(r"task_[A-Za-z0-9_-]{1,80}", task_id):
            break
        try:
            cache[task_id] = reported_progress(client.progress(task_id)[-30:])
        except Exception:
            # 单任务状态读取失败不造成整个监控接口不可用。
            cache[task_id] = reported_progress([])

    runs = []
    for run in ordered_runs:
        task_rows = _run_tasks(run, index, cache)
        progress = run.get("progress") or {}
        phases = progress.get("phases") or {}
        phase_rows = [
            {"id": key, "name": title, "status": (phases.get(key) or {}).get("state", "not_started"),
             "percent": (phases.get(key) or {}).get("percent"),
             "task_count": (phases.get(key) or {}).get("task_count", 0)}
            for key, title in PHASES
            if key in phases
        ]
        runs.append({
            "run_id": run.get("run_id"),
            "goal": str(run.get("goal") or "")[:240],
            "status": run.get("status"),
            "phase": run.get("phase"),
            "overall_percent": progress.get("overall_percent"),
            "percent_source": "supervisor_phase_weighting",
            "phases": phase_rows,
            "tasks": task_rows,
            "task_count": len(task_rows),
            "blockers": [str(x)[:280] for x in (progress.get("blockers") or run.get("errors") or [])[:5]],
            "next_action": str(progress.get("next_action") or "")[:280],
            "updated_at": run.get("updated_at"),
        })

    by_worker: dict[str, list[dict[str, Any]]] = {}
    for row in bridge_tasks:
        name = str(row.get("agent_id") or "")
        if name:
            by_worker.setdefault(name, []).append(row)
    workers = []
    for agent in agents:
        name = str(agent.get("agent_id") or "")
        if not name:
            continue
        history = sorted(by_worker.get(name, []), key=lambda x: str(x.get("created_at") or ""), reverse=True)
        counts = Counter(str(t.get("status") or "unknown") for t in history)
        current = [t for t in history if t.get("status") in ACTIVE_STATUSES]
        recent = current[:3] or history[:2]
        summary_tasks = []
        for task in recent:
            task_id = str(task.get("task_id") or "")
            meta = task.get("metadata") or {}
            summary_tasks.append({
                "task_id": task_id,
                "title": str(task.get("title") or "")[:170],
                "status": task.get("status"),
                "run_id": str(meta.get("team_run_id") or "")[:90],
                "phase": str(meta.get("team_phase") or "")[:48],
                "progress": cache.get(task_id, reported_progress([])),
            })
        workers.append({
            "agent_id": name, "online": bool(agent.get("available")),
            "configured": bool(agent.get("configured")),
            "queueable": bool(agent.get("queueable")),
            "busy": len(current) > 0,
            "active_count": len(current),
            "sampled_task_count": len(history),
            "sampled_completed": counts["completed"],
            "sampled_failed": counts["failed"],
            "tasks": summary_tasks,
        })
    return {
        "bridge_available": bridge_available,
        "task_sample_size": len(bridge_tasks),
        "task_sample_note": "Bridge 最近一页任务；历史统计不是累计总量",
        "runs": runs, "workers": workers,
        "progress_note": "任务百分比只取 Worker 进度事件；项目百分比来自 Supervisor 阶段进度。",
    }
