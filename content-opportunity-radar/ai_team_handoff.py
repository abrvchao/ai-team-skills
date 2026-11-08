"""Radar → AI Team deterministic handoff package（确定性交接包）."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

from read_model import OpportunityReadStore


SCHEMA_VERSION = "radar-ai-team-handoff-v1"
CONTENT_BRIEF_CONTRACT_VERSION = "content-brief-v1"
CONTENT_BRIEF_REQUIRED_SECTIONS = [
    "目标受众",
    "核心内容角度",
    "建议标题",
    "内容大纲",
    "草稿开篇",
    "Evidence Gaps（证据缺口）",
    "Go / No-Go 建议",
]


def _deliverable_path(topic_id: str) -> str:
    """为 Radar Lite 交接成果生成稳定、安全、可重复的相对路径。"""
    raw = topic_id.split(":", 1)[-1].strip().casefold()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._") or "opportunity"
    return f".radar/lite/deliveries/{slug}/CONTENT_BRIEF.md"


def build_handoff(
    opportunity: Mapping[str, Any],
    research_pack: Mapping[str, Any],
) -> dict[str, Any]:
    """把 Radar 的机会快照和 Research Pack 封装成下游 AI Team 可直接消费的输入。"""
    if not opportunity:
        raise ValueError("opportunity is required")
    if research_pack.get("schema_version") != "research-pack-v1":
        raise ValueError("research_pack must be research-pack-v1")

    topic_id = str(opportunity.get("topic_id") or "")
    topic = str(opportunity.get("topic") or research_pack.get("topic") or "")
    if not topic_id or not topic:
        raise ValueError("opportunity must include topic_id and topic")

    citations = list(research_pack.get("citations") or [])
    if not citations:
        raise ValueError("handoff requires at least one traceable citation")

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "radar_run_id": opportunity.get("run_id"),
            "scope": opportunity.get("scope"),
            "topic_id": topic_id,
            "topic": topic,
            "snapshot_created_at": opportunity.get("created_at"),
        },
        "decision_context": {
            "rank": opportunity.get("rank"),
            "scores": dict(opportunity.get("scores") or {}),
            "confidence": opportunity.get("confidence"),
            "freshness": opportunity.get("freshness"),
            "reasons": list(opportunity.get("reasons") or []),
            "source_summary": list(opportunity.get("source_summary") or []),
        },
        "research_pack": dict(research_pack),
        "ai_team_contract": {
            "handoff_goal": (
                f"基于 Radar 已发现的机会“{topic}”和随附证据，形成一个可直接审阅的具体成果；"
                "不要要求用户先掌握该主题的执行方法。"
            ),
            "worker_rules": [
                "先读取 research_pack，再开始分析或实现。",
                "事实性陈述必须引用 research_pack 中存在的 citation_id。",
                "不得把 missing_dimensions（缺失维度）补写成已知事实。",
                "不得重新发明 Radar 的排名、分数或来源。",
                "如果证据不足，明确标记 EVIDENCE GAP（证据缺口），不要让用户补内部执行知识。",
            ],
            "user_role": (
                "用户只需要判断最终成果是否值得继续；"
                "不要求用户说明如何执行该机会。"
            ),
        },
        "deliverable_contract": {
            "version": CONTENT_BRIEF_CONTRACT_VERSION,
            "artifact_type": "review_ready_content_brief",
            "artifact_path": _deliverable_path(topic_id),
            "language": "zh-CN",
            "required_sections": list(CONTENT_BRIEF_REQUIRED_SECTIONS),
            "acceptance": [
                "保留 Radar source run、scope、rank、scores 和 confidence。",
                "所有事实性陈述只能使用 Research Pack 中存在的 citation_id。",
                "所有 missing_dimensions 必须继续显式标记为 EVIDENCE GAP，不得补写成事实。",
                "必须按 required_sections 输出清晰章节；分析正确但缺少交付章节不算完成。",
                "Go 只表示值得继续做该内容/交付物，不等于商业机会已验证。",
                "最终文件不得保留 AI_TEAM_ARTIFACT_STATUS: in_progress。",
            ],
        },
        "guardrails": {
            "facts_require_citation_ids": True,
            "preserve_radar_scores": True,
            "no_fresh_research_required": True,
            "missing_dimensions_must_not_be_invented": True,
        },
    }


def build_from_store(
    db_path: str | Path,
    *,
    topic_id: str,
    scope: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    with OpportunityReadStore(db_path) as store:
        opportunity = store.get_opportunity(
            topic_id,
            scope=scope,
            workspace_id=workspace_id,
        )
        if not opportunity:
            raise KeyError(f"opportunity not found: {topic_id}")
        research_pack = store.research_pack(
            topic_id,
            scope=scope,
            workspace_id=workspace_id,
        )
        if not research_pack:
            raise KeyError(f"research pack not found: {topic_id}")
    return build_handoff(opportunity, research_pack)


def render_markdown(handoff: Mapping[str, Any]) -> str:
    source = handoff.get("source") or {}
    decision = handoff.get("decision_context") or {}
    pack = handoff.get("research_pack") or {}
    contract = handoff.get("ai_team_contract") or {}
    deliverable = handoff.get("deliverable_contract") or {}
    lines = [
        f"# Radar → AI Team Handoff — {source.get('topic')}",
        "",
        f"- Radar run: {source.get('radar_run_id')}",
        f"- Scope: {source.get('scope')}",
        f"- Rank: {decision.get('rank')}",
        f"- Scores: {json.dumps(decision.get('scores') or {}, ensure_ascii=False)}",
        f"- Confidence: {decision.get('confidence')}",
        "",
        "## Why this opportunity surfaced",
        "",
    ]
    for reason in decision.get("reasons") or []:
        lines.append(f"- {reason}")

    lines.extend(["", "## AI Team goal", "", str(contract.get("handoff_goal") or "")])
    lines.extend(["", "## Worker rules", ""])
    for rule in contract.get("worker_rules") or []:
        lines.append(f"- {rule}")

    lines.extend(["", "## Deliverable contract（交付物合同）", ""])
    lines.append(f"- Version: {deliverable.get('version')}")
    lines.append(f"- Artifact type: {deliverable.get('artifact_type')}")
    lines.append(f"- Language: {deliverable.get('language')}")
    lines.append("- Required sections（必需章节）:")
    for section in deliverable.get("required_sections") or []:
        lines.append(f"  - {section}")
    lines.append("- Acceptance（验收条件）:")
    for item in deliverable.get("acceptance") or []:
        lines.append(f"  - {item}")

    lines.extend(["", "## Evidence", ""])
    for citation in pack.get("citations") or []:
        cid = citation.get("citation_id")
        title = citation.get("title") or citation.get("source") or "Untitled"
        provider = citation.get("provider") or "unknown"
        url = citation.get("url") or ""
        lines.append(f"- [{cid}] {title} ({provider})" + (f" — {url}" if url else ""))

    missing = (pack.get("coverage") or {}).get("missing_dimensions") or []
    lines.extend(["", "## Evidence gaps", ""])
    if missing:
        lines.extend(f"- {item}" for item in missing)
    else:
        lines.append("- None across the core evidence dimensions.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Radar → AI Team handoff package")
    parser.add_argument("--db", default=".radar/radar.db")
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--scope")
    parser.add_argument("--workspace-id")
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    payload = build_from_store(
        args.db,
        topic_id=args.topic_id,
        scope=args.scope,
        workspace_id=args.workspace_id,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "markdown":
        output.write_text(render_markdown(payload), encoding="utf-8")
    else:
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
