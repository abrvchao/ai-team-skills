"""Human-readable release report（人类可读发布报告） generated from truthful run state."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _read_text(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _extract_section(text: str, heading: str, limit: int = 18) -> list[str]:
    """Extract one Markdown section（提取一个 Markdown 小节）, bounded for readability."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith(heading.lower()):
            start = index + 1
            break
    if start is None:
        return []
    out: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip():
            out.append(line.rstrip())
        if len(out) >= limit:
            break
    return out


def _test_evidence(run: dict[str, Any]) -> list[str]:
    # 中文说明：只提取已经写入 artifact 的测试事实，不根据文件修改情况猜测“测试应该通过”。
    paths: list[str] = []
    for entry in [run.get("implementation"), run.get("validation")]:
        if entry and entry.get("artifact"):
            paths.append(entry["artifact"])
    for item in run.get("repairs") or []:
        if item.get("artifact"):
            paths.append(item["artifact"])
    rows: list[str] = []
    seen: set[str] = set()
    for path in paths:
        text = _read_text(path)
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                continue
            if re.search(r"\b(Ran\s+\d+\s+tests?|PASS|OK\b|mismatches?\s*=|mismatches?\b)", clean, re.I):
                if clean not in seen:
                    seen.add(clean)
                    rows.append(clean[:240])
            if len(rows) >= 10:
                return rows
    return rows


def render_release_report(run: dict[str, Any]) -> str:
    status = str(run.get("status") or "unknown")
    verdict = str(run.get("implementation_validation_verdict") or "not_recorded")
    progress = (run.get("progress") or {}).get("overall_percent", 0)
    contract = run.get("product_contract") or {}

    implementation_text = _read_text((run.get("implementation") or {}).get("artifact"))
    changed = _extract_section(implementation_text, "## CHANGED_FILES")
    if not changed and run.get("repairs"):
        changed = _extract_section(_read_text(run["repairs"][-1].get("artifact")), "## CHANGED_FILES")

    lines = [
        f"# Release Report（发布报告）— {run.get('run_id', 'unknown')}",
        "",
        "## Executive Summary（执行摘要）",
        "",
        f"- Final status（最终状态）: **{status}**",
        f"- Run progress（运行进度）: **{progress}%**",
        f"- Independent validation verdict（独立验证结论）: **{verdict}**",
        f"- Product goal（产品目标）: {run.get('goal') or '未记录'}",
        f"- Workspace（工作区）: `{run.get('workspace') or ''}`",
        "",
        "## Acceptance Contract（验收合同）",
        "",
    ]
    for item in contract.get("acceptance") or []:
        lines.append(f"- {item}")
    if not contract.get("acceptance"):
        lines.append("- 未记录显式验收项。")

    lines.extend(["", "## Worker Contributions（执行器贡献）", ""])
    for role_owner, entry in (run.get("primary") or {}).items():
        role = entry.get("role_id") or role_owner
        worker = entry.get("worker_id") or role_owner
        lines.append(
            f"- `{role}`（角色） → `{worker}`（执行器）: {entry.get('status')}；"
            f"artifact（成果）: `{entry.get('artifact') or entry.get('salvaged_artifact') or 'none'}`"
        )

    lines.extend(["", "## Change Summary（变更摘要）", ""])
    if changed:
        lines.extend(changed)
    else:
        lines.append("- 未从实现/修复报告中提取到结构化 CHANGED_FILES（变更文件）小节。")

    lines.extend(["", "## Validation & Tests（验证与测试）", ""])
    evidence = _test_evidence(run)
    if evidence:
        lines.extend(f"- {row}" for row in evidence)
    else:
        lines.append("- 未找到可机器提取的测试结果；请查看下方原始成果路径。")

    validations = list(run.get("validation_history") or [])
    if run.get("validation"):
        validations.append(run["validation"])
    for index, entry in enumerate(validations, 1):
        lines.append(
            f"- Validation {index}（验证 {index}）: worker=`{entry.get('agent_id')}`, "
            f"status={entry.get('status')}, verdict={entry.get('verdict') or entry.get('reported_verdict') or 'unknown'}"
        )

    lines.extend(["", "## Failures & Recovery（失败与恢复）", ""])
    failures: list[str] = []
    for role_owner, entry in (run.get("primary") or {}).items():
        for attempt in entry.get("attempts") or []:
            if attempt.get("status") in {"failed", "cancelled"}:
                failures.append(
                    f"Primary role `{entry.get('role_id') or role_owner}`: worker `{attempt.get('worker_id')}` "
                    f"{attempt.get('status')} ({attempt.get('failure_class') or 'runtime_failure'})"
                )
    for row in failures:
        lines.append(f"- {row}")
    if not failures:
        lines.append("- No terminal primary-worker failure recorded（没有记录主角色执行器终止失败）。")
    for repair in run.get("repairs") or []:
        lines.append(
            f"- Repair attempt {repair.get('attempt')}（修复第 {repair.get('attempt')} 轮）: "
            f"worker=`{repair.get('agent_id')}`, status={repair.get('status')}, "
            f"repair_status={repair.get('repair_status') or 'unknown'}"
        )
    for item in run.get("resolved_errors") or []:
        lines.append(f"- Resolved（已解决）: {item.get('resolution')}; items={item.get('items') or []}")
    for item in run.get("errors") or []:
        lines.append(f"- Unresolved blocker（未解决阻塞）: {item}")

    lines.extend(["", "## Artifact Index（成果索引）", ""])
    artifact_paths: list[str] = []
    for path in [run.get("final_artifact"), (run.get("implementation") or {}).get("artifact")]:
        if path and path not in artifact_paths:
            artifact_paths.append(path)
    for item in run.get("repairs") or []:
        if item.get("artifact") and item["artifact"] not in artifact_paths:
            artifact_paths.append(item["artifact"])
    for item in validations:
        if item.get("artifact") and item["artifact"] not in artifact_paths:
            artifact_paths.append(item["artifact"])
    for path in artifact_paths:
        lines.append(f"- `{path}`")

    next_action = (run.get("progress") or {}).get("next_action") or "inspect current product plan"
    lines.extend([
        "",
        "## Next Action（下一动作）",
        "",
        f"- {next_action}",
        "",
        "---",
        "Generated deterministically from AI Team run state（由 AI Team 运行状态确定性生成）.",
    ])
    return "\n".join(lines) + "\n"


def generate_release_report(run: dict[str, Any], output_path: str | Path | None = None) -> dict[str, Any]:
    run_root = Path(run.get("run_root") or ".")
    path = Path(output_path) if output_path else run_root / "release_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_release_report(run), encoding="utf-8")
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "validation_verdict": run.get("implementation_validation_verdict"),
        "report_path": str(path),
    }
