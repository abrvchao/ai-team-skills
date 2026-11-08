"""AI Team Worker 自动变更检测和任务交接凭证。

分开处理两件事：
1) 成员变动：只记事实，不因新成员上线而抢占正在执行的任务；
2) 失败重派：保存前任快照、交接内容和接任状态，防止假完成。
全部持久化在已有 AI Team 状态目录，不触碰业务数据库。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix="handoff-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        os.chmod(filename, 0o600)
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def normalized_roster(agents: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """仅存可公开的状态字段；绝不持久化 token、服务端响应和账号凭据。"""
    result: dict[str, dict[str, Any]] = {}
    for agent in agents:
        name = str(agent.get("agent_id") or "").strip()
        if not name or not re.fullmatch(r"[a-z0-9_-]{1,64}", name):
            continue
        capabilities = sorted({
            str(x) for x in agent.get("capabilities", [])
            if isinstance(x, str) and len(x) <= 80
        })
        result[name] = {
            "configured": bool(agent.get("configured")),
            "available": bool(agent.get("available")),
            "queueable": bool(agent.get("queueable")),
            "capabilities": capabilities,
        }
    return result


def roster_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """对照角色清单：上线/掉线/退役/能力变化，不重复生成相同事件。"""
    changes = []
    for agent_id in sorted(set(before) | set(after)):
        previous, current = before.get(agent_id), after.get(agent_id)
        if previous is None:
            kind = "worker_registered"
        elif current is None:
            kind = "worker_removed"
        elif previous == current:
            continue
        elif previous.get("available") != current.get("available"):
            kind = "worker_online" if current.get("available") else "worker_offline"
        elif previous.get("capabilities") != current.get("capabilities"):
            kind = "worker_capabilities_changed"
        else:
            kind = "worker_configuration_changed"
        changes.append({
            "agent_id": agent_id, "kind": kind,
            "before": previous, "after": current, "detected_at": _now(),
            "action": "evaluate_affected_tasks_without_preempting_healthy_work",
        })
    return changes


class WorkerRosterMonitor:
    """由 Supervisor daemon 的现有轮询触发，不再创建额外后台轮询器。"""

    def __init__(self, state_dir: str | Path) -> None:
        root = Path(state_dir)
        self.state_path = root / "_worker-roster.json"
        self.changes_path = root / "_worker-roster-events.jsonl"

    def observe(self, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        current = normalized_roster(rows)
        if not current:
            # Bridge 异常空列表不能被误判为所有 Worker 同时下线。
            return []
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            previous = state.get("agents", {})
        else:
            # 首次观察只建立基线；旧项目不会凭空出现“所有角色新上线”风暴。
            _atomic_json(self.state_path, {"version": 1, "agents": current,
                                          "observed_at": _now()})
            return []
        changes = roster_changes(previous, current)
        if not changes:
            return []
        # 顺序：事件日志先写，再原子提交最新状态；日志损坏不得静默吞掉。
        self.changes_path.parent.mkdir(parents=True, exist_ok=True)
        with self.changes_path.open("a", encoding="utf-8") as file:
            for event in changes:
                file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        _atomic_json(self.state_path, {"version": 1, "agents": current,
                                      "observed_at": _now()})
        return changes


def save_handoff_state(handoff: Mapping[str, Any]) -> None:
    """运行状态与交接凭证同步，避免磁盘上留下过时的交接状态。"""
    manifest = handoff.get("manifest")
    if manifest:
        _atomic_json(Path(str(manifest)), handoff)


def prepare_handoff(
    *,
    run: Mapping[str, Any],
    phase: str,
    role_id: str,
    role_owner: str,
    old_entry: Mapping[str, Any],
    from_worker: str,
    to_worker: str,
    to_workspace: str | Path,
    reason: str,
    attempt: int,
    previous_artifact: str | None = None,
) -> dict[str, Any]:
    """在新任务提交前生成接任人可见的交接清单，冻结前任任务 ID/进度。

    禁止把原始错误信息或可能包含敏感值的 API 响应复制到交接正文。
    """
    safe = (phase, role_id, role_owner, from_worker, to_worker)
    if not all(re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", str(x)) for x in safe):
        raise ValueError("handoff_invalid_identifier")
    if not 1 <= attempt <= 20:
        raise ValueError("handoff_invalid_attempt")
    source_task = str(old_entry.get("task_id") or "")
    if not re.fullmatch(r"task_[a-zA-Z0-9_-]{1,100}", source_task):
        # 历史测试中的 task_1 也符合；缺失任务标识时不能伪造交接。
        raise ValueError("handoff_missing_source_task")
    root = Path(run["run_root"])
    destination = Path(to_workspace).resolve()
    if not destination.is_relative_to(root.resolve()):
        # validation 的工作目录可能是项目根目录，不是 run_root；
        # 交接文件实际仍在 run_root 里，防止写入产品根目录。
        destination = root / "handoffs" / "recipients" / phase / role_id
    ident = f"{phase}-{role_id}-{attempt:02d}-{source_task}"
    previous_attempts = [{
        "worker_id": str(item.get("worker_id") or ""),
        "task_id": str(item.get("task_id") or ""),
        "status": str(item.get("status") or ""),
        "failure_class": str(item.get("failure_class") or ""),
    } for item in list(old_entry.get("attempts") or [])[-8:]]
    record = {
        "version": "worker-handoff-v1",
        "handoff_id": ident,
        "run_id": str(run["run_id"]),
        "phase": phase, "role_id": role_id, "role_owner": role_owner,
        "from_worker": from_worker, "to_worker": to_worker,
        "source_task_id": source_task,
        "source_status": str(old_entry.get("status") or ""),
        "source_started_at": old_entry.get("started_at"),
        "source_finished_at": old_entry.get("finished_at"),
        "source_workspace": str(old_entry.get("workspace") or ""),
        "failure_class": str(reason or "unknown"),
        "attempt": attempt,
        "previous_attempts": previous_attempts,
        "previous_artifact": str(previous_artifact or ""),
        "acceptance": list((run.get("product_contract") or {}).get("acceptance") or []),
        "new_task_id": None,
        "status": "prepared",
        "claimed_at": None,
        "delivered_at": None,
        "created_at": _now(),
        "next_action": "assignee_reviews_handoff_and_delivers_original_acceptance",
    }
    destination.mkdir(parents=True, exist_ok=True)
    # 记录冻结在单独的 handoffs/，不重写被替换 Worker 的原始文件。
    manifest = root / "handoffs" / (ident + ".json")
    if manifest.exists():
        raise FileExistsError("handoff_already_exists")
    _atomic_json(manifest, record)
    lines = [
        "# AI Team 任务交接（Worker Handoff）",
        f"原任务：{source_task} / 原 Worker：{from_worker}",
        f"接任 Worker：{to_worker} / 阶段：{phase} / 角色：{role_id}",
        f"前任状态：{record['source_status']} / 分类原因：{record['failure_class']}",
        f"前任产物（仅限已验收的文件）：{record['previous_artifact'] or '无'}",
        "## 已知过程",
    ]
    for item in previous_attempts:
        lines.append(
            "- " + str(item["worker_id"]) + " / " + str(item["task_id"]) +
            " / " + str(item["status"]) + " / " + str(item["failure_class"])
        )
    lines += [
        "## 接任必须完成",
        "1. 保留前任任务 ID 和可用证据，不从零捏造已执行步骤。",
        "2. 逐条核查原验收标准；有遗漏必须明确标记。",
        "3. 交付可核验的最终产物，不能把已领取任务当作完成。",
        "4. 不重复调用被明确暂停/计费受限的模型。",
        "## 原验收标准",
    ] + ["- " + str(x) for x in record["acceptance"]]
    document = destination / "HANDOFF.md"
    # 排他创建防止其他 Worker 提前放置 symlink，保证不会越界覆盖源文件。
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(document, flags, 0o600), "w", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")
    return {"record": record, "manifest": str(manifest),
            "document": str(document), "prompt_excerpt": "\n".join(lines)[:6000]}


def update_handoffs(run: dict[str, Any], *, artifact_ready) -> None:
    """以真实任务状态和可用产物推进交接，不以进度百分比臆造确认。"""
    for handoff in run.get("handoffs", []):
        phase = handoff.get("phase")
        entry = ((run.get("primary") or {}).get(handoff["role_owner"])
                 if phase == "primary" else run.get("validation"))
        if not entry or handoff.get("new_task_id") != entry.get("task_id"):
            continue  # 历史交接不能被后续角色覆盖。
        state = handoff.get("status")
        if state in {"delivered", "failed"}:
            continue
        before = handoff.get("status")
        if entry.get("status") == "completed" and artifact_ready(entry, phase):
            handoff["status"] = "delivered"
            handoff["delivered_at"] = _now()
        elif entry.get("status") in {"failed", "cancelled"}:
            handoff["status"] = "failed"
            handoff["failed_at"] = _now()
        elif entry.get("started") or entry.get("started_at"):
            if state in {"assigned", "prepared"}:
                handoff["status"] = "in_progress"
                handoff["claimed_at"] = _now()
        if handoff.get("status") != before:
            save_handoff_state(handoff)
