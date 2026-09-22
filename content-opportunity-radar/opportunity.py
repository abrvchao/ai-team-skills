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


def _provider_balanced_mean(
    signals: Sequence[Signal],
    signal_types: set[str] | None = None,
    *,
    field_name: str = "normalized_value",
    default: float = 0.0,
) -> float:
    """Give each provider one vote regardless of how many rows it emitted."""
    grouped: dict[str, list[float]] = {}
    for signal in signals:
        if signal_types is not None and signal.signal_type not in signal_types:
            continue
        grouped.setdefault(signal.provider, []).append(float(getattr(signal, field_name)))

    provider_means = [_mean(values, default) for values in grouped.values()]
    return _mean(provider_means, default)


def _freshness(signals: Sequence[Signal]) -> float:
    if not signals:
        return 0.0

    grouped: dict[str, list[float]] = {}
    for row in signals:
        freshness_score = clamp(
            100.0 * (1.0 - min(row.freshness_seconds, 604800) / 604800.0)
        )
        grouped.setdefault(row.provider, []).append(freshness_score)

    return _mean(
        (_mean(values, 0.0) for values in grouped.values()),
        0.0,
    )


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

    Rows are aggregated within each provider before cross-provider averaging so
    high-volume providers cannot dominate simply by emitting more events.
    Missing dimensions use neutral 50 and reduce confidence; they are never
    invented by an LLM.
    """
    demand = _provider_balanced_mean(
        signals,
        {"demand", "question", "pain"},
        default=0.0,
    )
    momentum = _provider_balanced_mean(
        signals,
        {"momentum", "media", "research"},
        default=0.0,
    )

    supply_signals = [row for row in signals if row.signal_type == "supply"]
    authority_signals = [row for row in signals if row.signal_type == "authority"]
    commercial_signals = [row for row in signals if row.signal_type == "commercial"]

    supply_observed = supply_gap is not None or bool(supply_signals)
    authority_observed = authority_fit is not None or bool(authority_signals)
    business_observed = business_fit is not None or bool(commercial_signals)

    if supply_gap is None:
        supply_value = _provider_balanced_mean(
            supply_signals,
            {"supply"},
            default=50.0,
        )
        supply_gap = 100.0 - supply_value if supply_signals else 50.0

    if authority_fit is None:
        authority_fit = _provider_balanced_mean(
            authority_signals,
            {"authority"},
            default=50.0,
        ) if authority_signals else 50.0

    if business_fit is None:
        business_fit = _provider_balanced_mean(
            commercial_signals,
            {"commercial"},
            default=50.0,
        ) if commercial_signals else 50.0

    feature_summary = summarize_signals(signals)
    observed_confidence = _provider_balanced_mean(
        signals,
        None,
        field_name="confidence",
        default=0.0,
    )
    missing_penalty = 5.0 * sum([
        not supply_observed,
        not authority_observed,
        not business_observed,
    ])
    confidence = clamp(
        0.55 * observed_confidence
        + 0.45 * feature_summary.cross_source_confirmation
        - missing_penalty
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
        reasons.append("Cross-source demand is elevated.")
    if not supply_observed:
        reasons.append("Supply-gap evidence is not yet connected; neutral value used.")
    if not authority_observed:
        reasons.append("Authority evidence is not yet connected; neutral value used.")
    if not business_observed:
        reasons.append("Business-fit evidence is not yet connected; neutral value used.")

    return Opportunity(
        topic_id=topic_id,
        topic=topic,
        score=clamp(score),
        components=components,
        evidence_ids=evidence_ids,
        reasons=reasons,
        feature_summary=feature_summary,
    )
