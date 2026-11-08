"""R3 design-partner usefulness session（R3 设计伙伴可用性验证会）."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

from read_model import OpportunityReadStore


SESSION_SCHEMA = "r3-user-session-v1"
RESULT_SCHEMA = "r3-user-session-result-v1"


def build_session(*, database: str, scope: str, limit: int = 5, participant_alias: str = "design-partner-1") -> dict[str, Any]:
    with OpportunityReadStore(database, read_only=True, initialize=False) as store:
        payload = store.list_opportunities(scope=scope, limit=limit)
    items = []
    for row in payload.get("items") or []:
        items.append({
            "rank": row.get("rank"),
            "topic_id": row.get("topic_id"),
            "topic": row.get("topic"),
            "usefulness_1_to_5": None,
            "actionable": None,
            "would_act": None,
            "action_note": "",
        })
    return {
        "schema_version": SESSION_SCHEMA,
        "participant_alias": participant_alias,
        "scope": scope,
        "run_id": (payload.get("run") or {}).get("run_id"),
        "instructions": "真人逐条判断 usefulness（有用程度）、actionable（是否可行动）、would_act（是否愿意实际采取行动）。AI 不得代填。",
        "items": items,
        "overall_note": "",
    }


def score_session(session: Mapping[str, Any]) -> dict[str, Any]:
    if session.get("schema_version") != SESSION_SCHEMA:
        raise ValueError(f"input must use {SESSION_SCHEMA}")
    items = list(session.get("items") or [])
    complete = bool(items)
    scores = []
    actionable = 0
    would_act = 0
    for item in items:
        score = item.get("usefulness_1_to_5")
        act = item.get("actionable")
        will = item.get("would_act")
        if not isinstance(score, int) or not 1 <= score <= 5 or act not in {True, False} or will not in {True, False}:
            complete = False
            continue
        scores.append(score)
        actionable += int(act)
        would_act += int(will)
    # 中文说明：产品关卡要求“真实会话 + 记录发现”，不是规定一个凭空阈值；这里只计算事实，不替用户定义成功标准。
    return {
        "schema_version": RESULT_SCHEMA,
        "participant_alias": session.get("participant_alias"),
        "scope": session.get("scope"),
        "run_id": session.get("run_id"),
        "item_count": len(items),
        "completed": complete,
        "mean_usefulness": round(mean(scores), 2) if scores and complete else None,
        "actionable_count": actionable if complete else None,
        "would_act_count": would_act if complete else None,
        "at_least_one_real_action_intent": bool(complete and would_act > 0),
        "overall_note": session.get("overall_note") or "",
    }


def render_sheet(session: Mapping[str, Any]) -> str:
    lines = [
        "# Radar R3 Design Partner Session（Radar R3 设计伙伴验证会）",
        "",
        f"- Participant alias（参与者代号）: `{session.get('participant_alias')}`",
        f"- Scope（范围）: `{session.get('scope')}`",
        f"- Radar run（Radar 运行）: `{session.get('run_id')}`",
        "- 规则：必须由真人填写，AI 不得代填。",
        "",
    ]
    for item in session.get("items") or []:
        score = item.get("usefulness_1_to_5")
        actionable = item.get("actionable")
        would_act = item.get("would_act")
        note = item.get("action_note") or ""
        lines.extend([
            f"## Rank {item.get('rank')} — {item.get('topic')}",
            "",
            f"- usefulness_1_to_5（有用程度 1-5）: `{json.dumps(score)}`",
            f"- actionable（是否可行动）: `{json.dumps(actionable)}`",
            f"- would_act（是否愿意真的采取行动）: `{json.dumps(would_act)}`",
            f"- action_note（行动/原因备注）: `{note}`",
            "",
        ])
    lines.extend(["## Overall Note（整体备注）", "", "", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar R3 design partner session")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--db", default=".radar/radar.db")
    b.add_argument("--scope", required=True)
    b.add_argument("--participant", default="design-partner-1")
    b.add_argument("--output", required=True)
    b.add_argument("--sheet")
    s = sub.add_parser("score")
    s.add_argument("input")
    s.add_argument("--output")
    args = parser.parse_args()
    if args.command == "build":
        session = build_session(database=args.db, scope=args.scope, participant_alias=args.participant)
        Path(args.output).write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.sheet:
            Path(args.sheet).write_text(render_sheet(session), encoding="utf-8")
        print(args.output)
        return 0
    session = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = score_session(session)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["completed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
