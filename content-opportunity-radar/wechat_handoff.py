"""Adapter from Radar Research Pack V1 to WeChat skill research_pack.json.

This adapter is intentionally narrow:
- it never writes an article;
- it never invents an audience or objective;
- it converts only traceable Radar observations into source-backed claims;
- open evidence gaps remain open questions for the downstream writing skill.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def _source_type(evidence_class: str) -> str:
    if evidence_class == "first_party_observation":
        return "user-provided"
    if evidence_class == "media_index":
        return "secondary"
    return "primary"


def _credibility(evidence_class: str) -> str:
    """Conservative defaults scoped to the direct observation being cited."""
    if evidence_class in {
        "first_party_observation",
        "platform_market_metric",
    }:
        return "high"
    return "medium"


def _format_metric_claim(stat: Mapping[str, Any]) -> str:
    subject = str(stat.get("subject") or "Observed source").strip()
    metric = str(stat.get("metric") or "metric").strip()
    value = stat.get("value")
    return f"{subject}: observed {metric} = {value}."


def build_wechat_research_pack(
    radar_pack: Mapping[str, Any],
    *,
    audience: str,
    objective: str,
) -> dict[str, Any]:
    audience = audience.strip()
    objective = objective.strip()
    if not audience:
        raise ValueError("audience is required; do not invent it in the adapter")
    if not objective:
        raise ValueError("objective is required; do not invent it in the adapter")

    citation_to_source: dict[str, str] = {}
    sources: list[dict[str, Any]] = []

    for index, citation in enumerate(radar_pack.get("citations") or [], start=1):
        citation_id = str(citation.get("citation_id") or "")
        if not citation_id:
            continue
        source_id = f"S{index}"
        citation_to_source[citation_id] = source_id
        evidence_class = str(citation.get("evidence_class") or "context")
        sources.append(
            {
                "id": source_id,
                "title": str(
                    citation.get("title")
                    or citation.get("source")
                    or f"Radar evidence {citation_id}"
                ),
                "url": citation.get("url"),
                "publisher": str(
                    citation.get("source")
                    or citation.get("provider")
                    or "unknown"
                ),
                "published_at": _date(citation.get("published_at")),
                "accessed_at": (
                    _date(citation.get("retrieved_at"))
                    or _date(radar_pack.get("generated_at"))
                    or datetime.now(timezone.utc).date().isoformat()
                ),
                "source_type": _source_type(evidence_class),
                "credibility": _credibility(evidence_class),
                "radar_evidence_id": citation.get("evidence_id"),
                "radar_citation_id": citation_id,
                "radar_provider": citation.get("provider"),
                "radar_evidence_class": evidence_class,
                "radar_acquisition_method": citation.get("acquisition_method"),
                "radar_provenance": citation.get("provenance") or {},
            }
        )

    claims: list[dict[str, Any]] = []
    for stat in radar_pack.get("stats") or []:
        source_ids = [
            citation_to_source[citation_id]
            for citation_id in (stat.get("citation_ids") or [])
            if citation_id in citation_to_source
        ]
        if not source_ids:
            continue
        source = next(
            (item for item in sources if item["id"] == source_ids[0]),
            None,
        )
        confidence = (
            "high"
            if source and source.get("credibility") == "high"
            else "medium"
        )
        claims.append(
            {
                "id": f"C{len(claims) + 1}",
                "claim": _format_metric_claim(stat),
                "source_ids": source_ids,
                "confidence": confidence,
                "status": "verified",
                "radar_metric": stat.get("metric"),
                "radar_provider": stat.get("provider"),
            }
        )

    opportunity = radar_pack.get("opportunity") or {}
    insights = [
        f"[Radar interpretation] {reason}"
        for reason in (opportunity.get("reasons") or [])
        if str(reason).strip()
    ]

    open_questions: list[str] = []
    seen: set[str] = set()
    for row in radar_pack.get("key_questions") or []:
        question = str(row.get("text") or "").strip()
        if question and question.casefold() not in seen:
            seen.add(question.casefold())
            open_questions.append(question)

    for dimension in (
        (radar_pack.get("coverage") or {}).get("missing_dimensions") or []
    ):
        question = f"[Evidence gap] Need additional evidence for {dimension}."
        if question.casefold() not in seen:
            seen.add(question.casefold())
            open_questions.append(question)

    as_of = (
        _date(radar_pack.get("generated_at"))
        or datetime.now(timezone.utc).date().isoformat()
    )

    return {
        "topic": str(radar_pack.get("topic") or "").strip(),
        "as_of": as_of,
        "audience": audience,
        "objective": objective,
        "sources": sources,
        "claims": claims,
        "insights": insights,
        "open_questions": open_questions,
        "radar_handoff": {
            "schema_version": radar_pack.get("schema_version"),
            "topic_id": radar_pack.get("topic_id"),
            "rank_score": radar_pack.get("rank_score"),
            "opportunity": opportunity,
            "coverage": radar_pack.get("coverage") or {},
            "traceability": radar_pack.get("traceability") or {},
            "guardrails": radar_pack.get("guardrails") or {},
            "handoff_type": "radar_seed_research_pack",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Radar Research Pack to WeChat skill research_pack.json"
    )
    parser.add_argument("input", help="Radar research-pack-v1 JSON file")
    parser.add_argument("--audience", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--output", default="research_pack.json")
    args = parser.parse_args()

    radar_pack = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = build_wechat_research_pack(
        radar_pack,
        audience=args.audience,
        objective=args.objective,
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
