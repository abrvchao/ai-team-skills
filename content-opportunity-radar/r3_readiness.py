"""Radar R3 readiness control plane（Radar R3 就绪度控制面）."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from r3_seed_compare import score_compare
from r3_gsc_evidence import verify_gsc_report
from r3_user_session import score_session
from r3_validation import score_batch
from workspace import WorkspaceStore


READINESS_SCHEMA = "r3-readiness-v1"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def build_readiness(
    *,
    database: str = ".radar/radar.db",
    r3_root: str | Path = ".radar/r3",
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(r3_root)
    env = os.environ if environ is None else environ

    # 1) Human precision gate（人工精度门槛）
    label_batch_path = root / "human-label-pack" / "batch.json"
    label_batch = _load(label_batch_path)
    if label_batch:
        try:
            label_result = score_batch(label_batch)
        except Exception as exc:
            label_result = {"passed": False, "error": str(exc)}
    else:
        label_result = {"passed": False, "error": "label batch missing"}
    human_gate = {
        "id": "human_precision",
        "label": "Human precision@5（人工前五精度）",
        "preparation_ready": label_batch is not None,
        "passed": bool(label_result.get("passed")),
        "human_labeled": int(label_result.get("total_labeled_opportunities") or 0),
        "unique_labeled_topics": int(label_result.get("unique_labeled_topics") or 0),
        "micro_precision_at_5": label_result.get("micro_precision_at_k"),
        "artifact": str(label_batch_path),
        "blocker": None,
    }
    if not human_gate["passed"]:
        human_gate["blocker"] = "real human labels required（需要真人标注）"

    # 2) GSC first-party gate（GSC 第一方门槛）
    workspace_count = 0
    configured_gsc = []
    try:
        with WorkspaceStore(database) as store:
            workspaces = store.list(include_disabled=False)
        workspace_count = len(workspaces)
        configured_gsc = [w for w in workspaces if w.gsc_site_url]
    except Exception:
        workspaces = []
    gsc_report_path = root / "gsc" / "result.json"
    gsc_result = _load(gsc_report_path) or {}
    token_present = bool(str(env.get("GSC_ACCESS_TOKEN") or "").strip())
    # 中文说明：不能相信 result.json 的 passed；还必须重新核验可信 SQLite
    # GSC 官方 OAuth 事件记录、当前工作区站点、最新采集 run 和来源元数据。
    gsc_passed = (bool(configured_gsc) and token_present
                  and verify_gsc_report(gsc_result, database=database))
    gsc_gate = {
        "id": "gsc_first_party",
        "label": "GSC first-party validation（GSC 第一方验证）",
        "preparation_ready": True,
        "passed": gsc_passed,
        "workspace_count": workspace_count,
        "gsc_workspace_count": len(configured_gsc),
        "runtime_token_present": token_present,
        "credential_value_exposed": False,
        "artifact": str(gsc_report_path),
        "blocker": None,
    }
    if not gsc_passed:
        if not configured_gsc:
            gsc_gate["blocker"] = "GSC-enabled workspace required（需要配置 GSC 的工作区）"
        elif not token_present:
            gsc_gate["blocker"] = "runtime GSC token required（需要运行时 GSC 令牌）"
        else:
            gsc_gate["blocker"] = "real authorized GSC run required（需要真实授权 GSC 运行）"

    # 3) Design partner gate（设计伙伴门槛）
    session_path = root / "design-partner" / "session.json"
    session = _load(session_path)
    if session:
        try:
            session_result = score_session(session)
        except Exception as exc:
            session_result = {"completed": False, "error": str(exc)}
    else:
        session_result = {"completed": False, "error": "session missing"}
    design_gate = {
        "id": "design_partner",
        "label": "Design Partner session（设计伙伴验证会）",
        "preparation_ready": session is not None,
        "passed": bool(session_result.get("completed")),
        "mean_usefulness": session_result.get("mean_usefulness"),
        "actionable_count": session_result.get("actionable_count"),
        "would_act_count": session_result.get("would_act_count"),
        "artifact": str(session_path),
        "blocker": None,
    }
    if not design_gate["passed"]:
        design_gate["blocker"] = "real target-user session required（需要真实目标用户验证会）"

    # 4) Native vs experimental gate（原生 vs 实验对照门槛）
    experimental_path = root / "seed-compare" / "aihot-experimental.json"
    experimental = _load(experimental_path)
    if label_batch and experimental:
        try:
            compare_result = score_compare(native_batch=label_batch, experimental=experimental)
        except Exception as exc:
            compare_result = {"complete": False, "error": str(exc)}
    else:
        compare_result = {"complete": False, "error": "comparison inputs missing"}
    compare_gate = {
        "id": "native_vs_experimental",
        "label": "Native vs AIHOT comparison（原生 vs AIHOT 对照）",
        "preparation_ready": label_batch is not None and experimental is not None,
        "passed": bool(compare_result.get("complete")),
        "comparison": compare_result.get("comparison"),
        "precision_delta": compare_result.get("precision_delta_experimental_minus_native"),
        "automatic_provider_promotion": False,
        "artifact": str(experimental_path),
        "blocker": None,
    }
    if not compare_gate["passed"]:
        compare_gate["blocker"] = "human labels required for both queues（两组队列都需要真人标签）"

    gates = [human_gate, gsc_gate, design_gate, compare_gate]
    preparation_complete = all(bool(gate.get("preparation_ready")) for gate in gates)
    passed_count = sum(bool(gate.get("passed")) for gate in gates)
    next_actions = [gate["blocker"] for gate in gates if gate.get("blocker")]

    return {
        "schema_version": READINESS_SCHEMA,
        "product": "Content Opportunity Radar（内容机会雷达）",
        "stage": "R3 — Real-user validation（真实用户验证）",
        "preparation_complete": preparation_complete,
        "passed_gates": passed_count,
        "total_gates": len(gates),
        "stage_progress": f"{passed_count}/{len(gates)}",
        "stage_percent": round(100 * passed_count / len(gates)) if gates else 0,
        "r3_complete": passed_count == len(gates),
        "gates": gates,
        "next_actions": next_actions,
        "truth_note": "Preparation readiness is not gate completion（准备就绪不等于关卡完成）.",
    }


def render_markdown(readiness: dict[str, Any]) -> str:
    lines = [
        "# Radar R3 Readiness（Radar R3 就绪度）",
        "",
        f"- Preparation complete（准备完成）: **{readiness.get('preparation_complete')}**",
        f"- R3 gate progress（R3 关卡进度）: **{readiness.get('stage_progress')} ({readiness.get('stage_percent')}%)**",
        f"- R3 complete（R3 完成）: **{readiness.get('r3_complete')}**",
        "- Truth rule（真相规则）: 准备就绪不等于真实验收通过。",
        "",
        "## Gates（关卡）",
        "",
    ]
    for gate in readiness.get("gates") or []:
        state = "PASS（通过）" if gate.get("passed") else "PENDING（待完成）"
        lines.append(f"### {gate.get('label')} — {state}")
        lines.append("")
        lines.append(f"- Preparation ready（准备就绪）: `{gate.get('preparation_ready')}`")
        lines.append(f"- Artifact（成果）: `{gate.get('artifact')}`")
        if gate.get("blocker"):
            lines.append(f"- Blocker（阻塞）: {gate.get('blocker')}")
        lines.append("")
    lines.extend(["## Next Actions（下一动作）", ""])
    for action in readiness.get("next_actions") or []:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar R3 readiness control plane")
    parser.add_argument("--db", default=".radar/radar.db")
    parser.add_argument("--r3-root", default=".radar/r3")
    parser.add_argument("--output", default=".radar/r3/R3_READINESS.json")
    parser.add_argument("--markdown", default=".radar/r3/R3_READINESS.md")
    args = parser.parse_args()
    readiness = build_readiness(database=args.db, r3_root=args.r3_root)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.markdown).write_text(render_markdown(readiness), encoding="utf-8")
    print(json.dumps({
        "preparation_complete": readiness["preparation_complete"],
        "stage_progress": readiness["stage_progress"],
        "stage_percent": readiness["stage_percent"],
        "r3_complete": readiness["r3_complete"],
        "output": args.output,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
