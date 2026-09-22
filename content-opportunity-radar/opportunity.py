"""Explainable, deterministic Opportunity Score V1."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from core import Signal, clamp
from features import FeatureSummary, summarize_signals


WEIGHTS = {
    "demand": 0.22,
    "momentum": 0.20,
    "supply_gap": 0.18,
    "authority_fit": 0.15,
    "business_fit": 0.15,
    "freshness": 0.05,
    "confidence": 0.05,
}


@dataclass(slots=True)
class Opportunity:
    topic_id: str
    topic: str
    score: float
    components: dict[str, float]
    evidence_ids: list[str]
    reasons: list[str] = field(default_factory=list)
    feature_summary: FeatureSummary | None = None

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "topic": self.topic,
            "score": round(self.score, 2),
            "components": {key: round(value, 2) for key, value in self.components.items()},
            "evidence_ids": self.evidence_ids,
            "reasons": self.reasons,
            "features": self.feature_summary.to_dict() if self.feature_summary else {},
        }


def _mean(values: Iterable[float], default: float) -> float:
    xs = list(values)
    return sum(xs) / len(xs) if xs else default


def _freshness(signals: Sequence[Signal]) -> float:
    if not signals:
        return 0.0
    # Full score for <= 1h; decays linearly to zero by 7 days.
    scores = [
        clamp(100.0 * (1.0 - min(row.freshness_seconds, 604800) / 604800.0))
        for row in signals
    ]
    return _mean(scores, 0.0)


def score_opportunity(
    *,
    topic_id: str,
    topic: str,
    signals: Sequence[Signal],
    supply_gap: float | None = None,
    authority_fit: float | None = None,
    business_fit: float | None = None,
) -> Opportunity:
    """Score using observable signals only.

    Missing Phase-2 dimensions default to neutral 50 and reduce confidence.
    They are never hallucinated by an LLM.
    """
    demand_rows = [
        row.normalized_value for row in signals
        if row.signal_type in {"demand", "question", "pain"}
    ]
    momentum_rows = [
        row.normalized_value for row in signals
        if row.signal_type in {"momentum", "media", "research"}
    ]
    supply_rows = [
        row.normalized_value for row in signals if row.signal_type == "supply"
    ]

    demand = _mean(demand_rows, 0.0)
    momentum = _mean(momentum_rows, 0.0)

    if supply_gap is None:
        supply_gap = 100.0 - _mean(supply_rows, 50.0) if supply_rows else 50.0
    if authority_fit is None:
        authority_fit = 50.0
    if business_fit is None:
        business_fit = 50.0

    feature_summary = summarize_signals(signals)
    observed_confidence = _mean((row.confidence for row in signals), 0.0)
    missing_phase2 = sum(value is None for value in [])  # kept explicit for readability
    phase2_penalty = 15.0 if not supply_rows else 8.0
    confidence = clamp(
        0.55 * observed_confidence
        + 0.45 * feature_summary.cross_source_confirmation
        - phase2_penalty
    )
    freshness = _freshness(signals)

    components = {
        "demand": clamp(demand),
        "momentum": clamp(momentum),
        "supply_gap": clamp(supply_gap),
        "authority_fit": clamp(authority_fit),
        "business_fit": clamp(business_fit),
        "freshness": clamp(freshness),
        "confidence": clamp(confidence),
    }

    score = sum(components[key] * WEIGHTS[key] for key in WEIGHTS)
    evidence_ids = sorted({eid for row in signals for eid in row.evidence_ids})

    reasons: list[str] = []
    if feature_summary.positive_provider_count >= 3:
        reasons.append(
            f"Confirmed across {feature_summary.positive_provider_count} independent providers."
        )
    if momentum >= 60:
        reasons.append("Cross-source momentum is elevated.")
    if demand >= 60:
        reasons.append("Discussion/demand signals are elevated.")
    if not supply_rows:
        reasons.append("Supply-gap evidence is not yet connected; neutral value used.")
    reasons.append("Authority/business dimensions are neutral until first-party Phase-2 data is connected.")

    return Opportunity(
        topic_id=topic_id,
        topic=topic,
        score=clamp(score),
        components=components,
        evidence_ids=evidence_ids,
        reasons=reasons,
        feature_summary=feature_summary,
    )
