"""Radar → AI Team standard intake（Radar → AI Team 标准接入器）."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from product_intake import ProductContractStore, _atomic_json


HANDOFF_SCHEMA_VERSION = "radar-ai-team-handoff-v1"


def _fallback_deliverable_path(source: dict[str, Any]) -> str:
    topic_id = str(source.get("topic_id") or source.get("topic") or "opportunity")
    raw = topic_id.split(":", 1)[-1].strip().casefold()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._") or "opportunity"
    return f".radar/lite/deliveries/{slug}/CONTENT_BRIEF.md"


def _safe_relative_artifact(value: str) -> str:
    path = Path(str(value or "").strip())
    if not str(path) or path.is_absolute() or ".." in path.parts:
        raise ValueError("deliverable artifact_path must be a safe workspace-relative path")
    return path.as_posix()


def draft_from_handoff(
    handoff_path: str | Path,
    *,
    workspace: str | Path,
    store: ProductContractStore,
    participants: list[str] | None = None,
) -> dict[str, Any]:
    """把确定性 Radar handoff 转成正式 Product Contract 草案。"""
    workspace_path = Path(workspace).expanduser().resolve()
    source_path = Path(handoff_path).expanduser().resolve()

    try:
        relative = source_path.relative_to(workspace_path)
    except ValueError as exc:
        raise ValueError("handoff must live inside the product workspace") from exc

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        raise ValueError(f"handoff must be {HANDOFF_SCHEMA_VERSION}")

    source = payload.get("source") or {}
    ai_contract = payload.get("ai_team_contract") or {}
    deliverable = payload.get("deliverable_contract") or {}
    guardrails = payload.get("guardrails") or {}

    topic = str(source.get("topic") or "").strip()
    goal = str(ai_contract.get("handoff_goal") or "").strip()
    if not topic or not goal:
        raise ValueError("handoff must include source.topic and ai_team_contract.handoff_goal")

    acceptance = list(deliverable.get("acceptance") or [])
    if not acceptance:
        raise ValueError("handoff must include deliverable_contract.acceptance")
    deliverable_path = _safe_relative_artifact(
        str(deliverable.get("artifact_path") or _fallback_deliverable_path(source))
    )

    worker_rules = list(ai_contract.get("worker_rules") or [])
    evidence_required = list(worker_rules)
    evidence_required.append(
        "Treat the supplied Radar Handoff Pack as authoritative context for source run, scores, citations, and evidence gaps."
    )

    draft = store.draft(
        idea=goal,
        workspace=str(workspace_path),
        product_name=f"Radar Handoff — {topic}",
        participants=participants,
    )
    draft["objective"] = goal
    draft["acceptance"] = acceptance
    draft["evidence_required"] = evidence_required
    draft["context_artifacts"] = [str(relative)]
    draft["deliverable_artifacts"] = [deliverable_path]
    draft["fallback_policy"] = "salvage|degrade"
    draft["resource_policy"]["mode"] = "stop_loss"
    draft["radar_handoff"] = {
        "schema_version": payload["schema_version"],
        "source": source,
        "deliverable_contract_version": deliverable.get("version"),
        "deliverable_artifacts": [deliverable_path],
        "facts_require_citation_ids": bool(guardrails.get("facts_require_citation_ids")),
    }
    draft["stop_conditions"] = list(draft.get("stop_conditions") or []) + [
        "Do not re-run Radar discovery or scoring inside this delivery run.",
        "Do not ask the user how to execute the opportunity; use the handoff evidence and AI Team workers.",
        "Do not invent missing evidence dimensions or citations.",
    ]
    _atomic_json(store.path(draft["contract_id"]), draft)
    return draft
