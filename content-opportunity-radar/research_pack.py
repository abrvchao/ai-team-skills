"""Deterministic Research Pack V1 for Content Opportunity Radar.

A Research Pack is a provenance-first handoff artifact for downstream AI agents.
It does not ask an LLM to invent facts, trend scores, questions, or citations.
Everything in the pack must trace back to the opportunity report's evidence.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


QUESTION_RE = re.compile(
    r"^\s*(how|why|what|when|where|which|who|can|could|should|does|do|is|are)\b",
    re.I,
)
PAIN_RE = re.compile(
    r"\b(error|fail(?:ed|ure|ing)?|broken|problem|issue|difficult|hard|"
    r"missing|need|request|support|cannot|can't|doesn't work|bug)\b",
    re.I,
)

DIMENSION_SIGNAL_TYPES = {
    "demand": {"demand", "question", "pain"},
    "momentum": {"momentum", "media", "research"},
    "supply_gap": {"supply"},
    "authority_fit": {"authority"},
    "business_fit": {"commercial"},
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_group(event: Mapping[str, Any]) -> str:
    provider = str(event.get("provider") or "")
    if provider == "gsc":
        return "first_party"
    if provider == "keyword_planner":
        return "market"
    if provider in {"github", "hackernews"}:
        return "community"
    if provider in {"google_news", "gdelt"}:
        return "media"
    if provider == "website":
        return "website"
    return "other"


def _event_text(event: Mapping[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "").strip()
        for key in ("title", "text")
        if event.get(key)
    ).strip()


def _is_question(event: Mapping[str, Any]) -> bool:
    text = _event_text(event)
    if not text:
        return False
    return "?" in text or bool(QUESTION_RE.search(text))


def _is_pain(event: Mapping[str, Any]) -> bool:
    text = _event_text(event)
    return bool(text and PAIN_RE.search(text))


def _dedupe_text_rows(rows: Sequence[dict], key: str = "text") -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for row in rows:
        normalized = re.sub(r"\s+", " ", str(row.get(key) or "")).strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(row)
    return result


def _dimension_coverage(signals: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for dimension, signal_types in DIMENSION_SIGNAL_TYPES.items():
        matched = [
            signal
            for signal in signals
            if str(signal.get("signal_type") or "") in signal_types
        ]
        result[dimension] = {
            "observed": bool(matched),
            "providers": sorted(
                {
                    str(signal.get("provider") or "")
                    for signal in matched
                    if signal.get("provider")
                }
            ),
            "signal_count": len(matched),
            "evidence_ids": sorted(
                {
                    str(evidence_id)
                    for signal in matched
                    for evidence_id in (signal.get("evidence_ids") or [])
                    if evidence_id
                }
            ),
        }
    return result


def _evidence_class(event: Mapping[str, Any]) -> str:
    """Describe evidence origin without pretending acquisition equals truth."""
    group = _source_group(event)
    acquisition = str(event.get("acquisition_method") or "")
    if group == "first_party":
        return "first_party_observation"
    if group == "market":
        return "platform_market_metric"
    if group == "community":
        return "community_direct"
    if group == "media":
        return "media_index"
    if group == "website":
        return "site_direct"
    if acquisition in {"official_api", "oauth_api"}:
        return "direct_api"
    return "context"


def build_action_card(
    *,
    topic: object,
    citations: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
    pain_signals: Sequence[Mapping[str, Any]],
    missing_dimensions: Sequence[str],
) -> dict[str, Any]:
    """Build one deterministic first-action card without inventing domain facts."""
    citation_ids = [str(row.get("citation_id")) for row in citations[:2] if row.get("citation_id")]
    refs = " + ".join(citation_ids) if citation_ids else "the available evidence（现有证据）"
    if pain_signals:
        focus = "把最强痛点/需求信号改写成一个具体用户问题"
    elif questions:
        focus = "选出最具体的已观察问题，写出一个有证据支持的回答方向"
    else:
        focus = "从证据中提取 3 条“发生了什么 / 谁在意 / 为什么现在”的事实"

    risk_reasons: list[str] = []
    if len(citations) < 2:
        risk_reasons.append("少于 2 条可追溯证据")
    if not questions and not pain_signals:
        risk_reasons.append("缺少明确问题/痛点信号")
    if len(missing_dimensions) >= 3:
        risk_reasons.append("核心证据维度缺口较大")

    # 中文说明：这里只评估“开始研究这一小步”的风险，不冒充完整内容制作工期或成功率预测。
    starter_risk = "high" if len(risk_reasons) >= 2 else ("medium" if risk_reasons else "low")
    return {
        "timebox_minutes": 30,
        "first_step": (
            f"用 30 分钟只看 {refs}，围绕 {str(topic or 'this topic')}：{focus}；"
            "不要先写长文，也不要扩展到新框架或新数据源。"
        ),
        "minimum_viable_output": (
            "一页 5 点 brief（简报）：1 个具体角度、1 个目标读者/使用者、"
            "2 条带 citation id（引文编号）的证据、1 个下一步问题。"
        ),
        "starter_risk": starter_risk,
        "risk_reasons": risk_reasons,
        "stop_rule": (
            "30 分钟结束时，如果仍写不出 2 条带引文编号的具体证据 + 1 个明确受众/问题，"
            "就停止扩写并把该机会降级。"
        ),
        "guardrail": "这是起步动作，不是完整制作工期或成功率预测。",
    }


def build_research_pack(
    report: Mapping[str, Any],
    *,
    max_citations: int = 40,
    max_questions: int = 10,
    max_pain_signals: int = 10,
    max_stats: int = 24,
) -> dict[str, Any]:
    """Build a traceable research artifact from one opportunity report."""
    opportunity = dict(report.get("opportunity") or {})
    signals = list(report.get("signals") or [])
    events = list(report.get("events") or [])

    requested_evidence = {
        str(item)
        for item in (opportunity.get("evidence_ids") or [])
        if item
    }
    event_by_id = {
        str(event.get("id")): event
        for event in events
        if event.get("id")
    }

    evidence_events = [
        event_by_id[evidence_id]
        for evidence_id in sorted(requested_evidence)
        if evidence_id in event_by_id
    ]
    evidence_events.sort(
        key=lambda event: (
            str(event.get("provider") or ""),
            str(event.get("published_at") or event.get("retrieved_at") or ""),
            str(event.get("title") or ""),
        )
    )

    citations: list[dict[str, Any]] = []
    citation_by_evidence: dict[str, str] = {}
    for index, event in enumerate(evidence_events[:max_citations], start=1):
        citation_id = f"C{index:03d}"
        evidence_id = str(event.get("id"))
        citation_by_evidence[evidence_id] = citation_id
        provenance = dict(event.get("provenance") or {})
        citations.append(
            {
                "citation_id": citation_id,
                "evidence_id": evidence_id,
                "provider": event.get("provider"),
                "source": event.get("source"),
                "source_group": _source_group(event),
                "evidence_class": _evidence_class(event),
                "title": event.get("title"),
                "url": event.get("url"),
                "published_at": event.get("published_at"),
                "retrieved_at": event.get("retrieved_at"),
                "language": event.get("language"),
                "country": event.get("country"),
                "acquisition_method": event.get("acquisition_method"),
                "metrics": dict(event.get("metrics") or {}),
                "provenance": provenance,
            }
        )

    questions: list[dict[str, Any]] = []
    pain_signals: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []

    for event in evidence_events:
        evidence_id = str(event.get("id") or "")
        citation_id = citation_by_evidence.get(evidence_id)
        if not citation_id:
            continue
        text = _event_text(event)

        if _is_question(event):
            questions.append(
                {
                    "text": text[:500],
                    "citation_ids": [citation_id],
                    "provider": event.get("provider"),
                }
            )

        if _is_pain(event):
            pain_signals.append(
                {
                    "text": text[:500],
                    "citation_ids": [citation_id],
                    "provider": event.get("provider"),
                }
            )

        for metric, value in sorted((event.get("metrics") or {}).items()):
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            stats.append(
                {
                    "metric": str(metric),
                    "value": numeric,
                    "subject": event.get("title") or event.get("external_id"),
                    "provider": event.get("provider"),
                    "citation_ids": [citation_id],
                }
            )

    questions = _dedupe_text_rows(questions)[:max_questions]
    pain_signals = _dedupe_text_rows(pain_signals)[:max_pain_signals]
    stats.sort(
        key=lambda row: (
            str(row.get("metric") or ""),
            -abs(float(row.get("value") or 0.0)),
            str(row.get("subject") or ""),
        )
    )
    stats = stats[:max_stats]

    coverage = _dimension_coverage(signals)
    observed_dimensions = [
        dimension
        for dimension, details in coverage.items()
        if details["observed"]
    ]
    missing_dimensions = [
        dimension
        for dimension, details in coverage.items()
        if not details["observed"]
    ]

    source_groups: dict[str, int] = {}
    provider_counts: dict[str, int] = {}
    for citation in citations:
        group = str(citation.get("source_group") or "other")
        provider = str(citation.get("provider") or "unknown")
        source_groups[group] = source_groups.get(group, 0) + 1
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

    reasons = list(opportunity.get("reasons") or [])
    component_scores = dict(opportunity.get("components") or {})

    resolvable_evidence = requested_evidence & set(event_by_id)
    cited_evidence = set(citation_by_evidence)
    traceability = {
        "requested_evidence_count": len(requested_evidence),
        "resolved_evidence_count": len(resolvable_evidence),
        "cited_evidence_count": len(citations),
        "unresolved_evidence_ids": sorted(requested_evidence - set(event_by_id)),
        "uncited_due_to_limit_ids": sorted(resolvable_evidence - cited_evidence),
        "resolution_coverage": (
            round(len(resolvable_evidence) / len(requested_evidence), 4)
            if requested_evidence
            else 1.0
        ),
        "citation_coverage": (
            round(len(cited_evidence) / len(requested_evidence), 4)
            if requested_evidence
            else 1.0
        ),
    }
    action_card = build_action_card(
        topic=report.get("topic"),
        citations=citations,
        questions=questions,
        pain_signals=pain_signals,
        missing_dimensions=missing_dimensions,
    )

    return {
        "schema_version": "research-pack-v1",
        "generated_at": _iso_now(),
        "topic": report.get("topic"),
        "topic_id": report.get("topic_id"),
        "rank_score": report.get("rank_score"),
        "discovery": report.get("discovery"),
        "opportunity": {
            "score": opportunity.get("score"),
            "components": component_scores,
            "reasons": reasons,
            "features": opportunity.get("features") or {},
        },
        "coverage": {
            "dimensions": coverage,
            "observed_dimensions": observed_dimensions,
            "missing_dimensions": missing_dimensions,
            "research_readiness": round(
                len(observed_dimensions) / len(DIMENSION_SIGNAL_TYPES),
                4,
            ),
        },
        "source_summary": {
            "citation_count": len(citations),
            "providers": provider_counts,
            "source_groups": source_groups,
        },
        "key_questions": questions,
        "pain_signals": pain_signals,
        "stats": stats,
        "citations": citations,
        "action_card": action_card,
        "traceability": traceability,
        "guardrails": {
            "trend_score_generated_by_llm": False,
            "topic_generated_by_llm": False,
            "facts_require_citation_ids": True,
            "missing_dimensions_must_not_be_invented": True,
            "citation_ids_reference_raw_evidence": True,
        },
    }


def render_markdown(pack: Mapping[str, Any]) -> str:
    """Render a compact human-readable view without adding unsupported claims."""
    opportunity = pack.get("opportunity") or {}
    coverage = pack.get("coverage") or {}
    lines = [
        f"# Research Pack — {pack.get('topic') or 'Unknown topic'}",
        "",
        f"- Opportunity Score: {opportunity.get('score')}",
        f"- Rank Score: {pack.get('rank_score')}",
        f"- Research readiness: {coverage.get('research_readiness')}",
        "",
        "## Score breakdown",
        "",
    ]

    for name, value in (opportunity.get("components") or {}).items():
        lines.append(f"- {name}: {value}")

    action = pack.get("action_card") or {}
    lines.extend(["", "## Action Card（行动卡）", ""])
    lines.append(f"- First step（第一步）: {action.get('first_step') or 'Not available.'}")
    lines.append(
        f"- Minimum viable output（最小成品）: "
        f"{action.get('minimum_viable_output') or 'Not available.'}"
    )
    lines.append(f"- Starter risk（起步风险）: {action.get('starter_risk') or 'unknown'}")
    lines.append(f"- Stop rule（停止条件）: {action.get('stop_rule') or 'Not available.'}")

    lines.extend(["", "## Key questions", ""])
    questions = pack.get("key_questions") or []
    if questions:
        for row in questions:
            refs = " ".join(f"[{cid}]" for cid in row.get("citation_ids") or [])
            lines.append(f"- {row.get('text')} {refs}".rstrip())
    else:
        lines.append("- No question-shaped evidence detected.")

    lines.extend(["", "## Pain signals", ""])
    pain = pack.get("pain_signals") or []
    if pain:
        for row in pain:
            refs = " ".join(f"[{cid}]" for cid in row.get("citation_ids") or [])
            lines.append(f"- {row.get('text')} {refs}".rstrip())
    else:
        lines.append("- No explicit pain-language evidence detected.")

    lines.extend(["", "## Evidence", ""])
    for citation in pack.get("citations") or []:
        title = citation.get("title") or citation.get("source") or "Untitled evidence"
        provider = citation.get("provider") or "unknown"
        url = citation.get("url")
        suffix = f" — {url}" if url else ""
        lines.append(
            f"- [{citation.get('citation_id')}] {title} ({provider}){suffix}"
        )

    missing = coverage.get("missing_dimensions") or []
    lines.extend(["", "## Evidence gaps", ""])
    if missing:
        for dimension in missing:
            lines.append(f"- {dimension}")
    else:
        lines.append("- None across the five core evidence dimensions.")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic Research Pack")
    parser.add_argument("input", help="JSON report from pipeline.py or one Radar opportunity")
    parser.add_argument("--output")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    pack = build_research_pack(report)
    rendered = (
        render_markdown(pack)
        if args.format == "markdown"
        else json.dumps(pack, ensure_ascii=False, indent=2)
    )

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
