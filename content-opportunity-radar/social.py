"""Platform-neutral China Social signal contract.

Provider adapters keep platform quirks/raw fields. This module only converts
normalized social observations into the existing Radar Signal vocabulary.

There is intentionally no "Social Hot Score".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Mapping, Sequence

from core import AcquisitionMethod, Signal, clamp, stable_id, utcnow
from features import cross_source_confirmation, source_diversity


class SocialMetric(str, Enum):
    MENTION_COUNT = "mention_count"
    NEW_CONTENT_COUNT = "new_content_count"
    ENGAGEMENT = "engagement"
    ENGAGEMENT_VELOCITY = "engagement_velocity"
    CREATOR_COUNT = "creator_count"
    CREATOR_DIVERSITY = "creator_diversity"
    QUESTION_COUNT = "question_count"
    PAIN_COUNT = "pain_count"
    COMPARISON_COUNT = "comparison_count"
    CONTENT_SUPPLY = "content_supply"
    CONTENT_VELOCITY = "content_velocity"
    SEARCH_RANK = "search_rank"
    HOT_RANK = "hot_rank"


METRIC_SIGNAL_TYPE: dict[SocialMetric, str | None] = {
    SocialMetric.MENTION_COUNT: "demand",
    SocialMetric.QUESTION_COUNT: "question",
    SocialMetric.PAIN_COUNT: "pain",
    SocialMetric.COMPARISON_COUNT: "demand",
    SocialMetric.ENGAGEMENT: "momentum",
    SocialMetric.ENGAGEMENT_VELOCITY: "momentum",
    SocialMetric.CONTENT_VELOCITY: "momentum",
    SocialMetric.SEARCH_RANK: "momentum",
    SocialMetric.HOT_RANK: "momentum",
    SocialMetric.NEW_CONTENT_COUNT: "supply",
    SocialMetric.CONTENT_SUPPLY: "supply",
    SocialMetric.CREATOR_COUNT: "supply",
    # Diversity is retained as a diagnostic feature. It is not silently
    # transformed into another score dimension.
    SocialMetric.CREATOR_DIVERSITY: None,
}

INVERSE_METRICS = {
    SocialMetric.SEARCH_RANK,
    SocialMetric.HOT_RANK,
}


@dataclass(slots=True)
class SocialObservation:
    """One provider-level metric observation for one resolved topic.

    normalized_value is optional. When absent, this module may derive a
    provider-relative percentile only when sufficient prior history is supplied.
    Missing normalization/history remains unknown; it is never coerced to zero.
    """

    provider: str
    source: str
    topic_id: str
    metric: SocialMetric
    value: float
    observed_at: datetime
    evidence_ids: list[str]
    acquisition_method: AcquisitionMethod
    confidence: float
    normalized_value: float | None = None
    language: str | None = "zh"
    geo: str | None = "CN"
    observation_id: str | None = None
    metadata: dict[str, str | float | int | bool | None] = field(default_factory=dict)

    def identity(self) -> str:
        if self.observation_id:
            return self.observation_id
        return stable_id(
            "social-observation",
            self.provider,
            self.source,
            self.topic_id,
            self.metric.value,
            self.observed_at.isoformat(),
            ",".join(sorted(set(self.evidence_ids))),
        )


@dataclass(slots=True)
class SocialNormalizationResult:
    signals: list[Signal]
    skipped: list[dict[str, str]]
    observations_seen: int
    observations_deduped: int

    def to_dict(self) -> dict:
        return {
            "signals": [row.to_dict() for row in self.signals],
            "skipped": list(self.skipped),
            "observations_seen": self.observations_seen,
            "observations_deduped": self.observations_deduped,
        }


@dataclass(slots=True)
class SocialFeatureSummary:
    provider_count: int
    evidence_count: int
    metric_count: int
    source_diversity: float
    cross_platform_confirmation: float
    creator_diversity: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "provider_count": self.provider_count,
            "evidence_count": self.evidence_count,
            "metric_count": self.metric_count,
            "source_diversity": round(self.source_diversity, 2),
            "cross_platform_confirmation": round(
                self.cross_platform_confirmation,
                2,
            ),
            "creator_diversity": (
                round(self.creator_diversity, 2)
                if self.creator_diversity is not None
                else None
            ),
        }


def history_percentile_score(
    current: float,
    history: Sequence[float],
    *,
    inverse: bool = False,
    minimum_points: int = 3,
) -> float | None:
    """Provider-relative empirical percentile against prior observations.

    This intentionally avoids cross-platform absolute-count comparisons.
    Fewer than minimum_points prior observations returns unknown.
    """

    prior = [float(item) for item in history]
    if len(prior) < minimum_points:
        return None

    if inverse:
        favorable = sum(1 for item in prior if current <= item)
    else:
        favorable = sum(1 for item in prior if current >= item)
    return clamp(100.0 * favorable / len(prior))


def dedupe_social_observations(
    observations: Iterable[SocialObservation],
) -> list[SocialObservation]:
    """Deduplicate exact provider/topic/metric observations deterministically."""

    unique: dict[str, SocialObservation] = {}
    for row in observations:
        unique.setdefault(row.identity(), row)
    return sorted(
        unique.values(),
        key=lambda row: (
            row.observed_at,
            row.provider,
            row.metric.value,
            row.identity(),
        ),
    )


def observation_to_signal(
    observation: SocialObservation,
    *,
    history: Sequence[float] | None = None,
    now: datetime | None = None,
) -> tuple[Signal | None, str | None]:
    signal_type = METRIC_SIGNAL_TYPE[observation.metric]
    if signal_type is None:
        return None, "diagnostic_metric_not_scored"

    normalized = observation.normalized_value
    if normalized is None:
        normalized = history_percentile_score(
            observation.value,
            history or [],
            inverse=observation.metric in INVERSE_METRICS,
        )
    if normalized is None:
        return None, "missing_normalization_or_history"

    clock = now or utcnow()
    freshness_seconds = max(
        0,
        int((clock - observation.observed_at).total_seconds()),
    )

    prior = [float(item) for item in (history or [])]
    baseline = sum(prior) / len(prior) if prior else None
    delta = (
        float(observation.value) - baseline
        if baseline is not None
        else None
    )

    return (
        Signal(
            id=stable_id(
                "social-signal",
                observation.identity(),
                signal_type,
            ),
            topic_id=observation.topic_id,
            entity_ids=[],
            signal_type=signal_type,
            source=observation.source,
            provider=observation.provider,
            observed_at=observation.observed_at,
            value=float(observation.value),
            normalized_value=clamp(normalized),
            confidence=clamp(observation.confidence),
            evidence_ids=sorted(set(observation.evidence_ids)),
            freshness_seconds=freshness_seconds,
            baseline=baseline,
            delta=delta,
            geo=observation.geo,
            language=observation.language,
        ),
        None,
    )


def normalize_social_observations(
    observations: Sequence[SocialObservation],
    *,
    history_by_key: Mapping[tuple[str, str, SocialMetric], Sequence[float]] | None = None,
    now: datetime | None = None,
) -> SocialNormalizationResult:
    """Normalize social observations without inventing missing measurements."""

    history_by_key = history_by_key or {}
    unique = dedupe_social_observations(observations)
    signals: list[Signal] = []
    skipped: list[dict[str, str]] = []

    for row in unique:
        signal, reason = observation_to_signal(
            row,
            history=history_by_key.get(
                (row.provider, row.topic_id, row.metric)
            ),
            now=now,
        )
        if signal is not None:
            signals.append(signal)
        else:
            skipped.append(
                {
                    "observation_id": row.identity(),
                    "provider": row.provider,
                    "metric": row.metric.value,
                    "reason": reason or "unknown",
                }
            )

    return SocialNormalizationResult(
        signals=signals,
        skipped=skipped,
        observations_seen=len(observations),
        observations_deduped=len(unique),
    )


def summarize_social_signals(
    signals: Sequence[Signal],
    *,
    observations: Sequence[SocialObservation] = (),
) -> SocialFeatureSummary:
    """Return diagnostics; Opportunity Score still consumes normal Signals."""

    providers = {row.provider for row in signals}
    evidence = {eid for row in signals for eid in row.evidence_ids}
    metrics = {
        row.metric.value
        for row in observations
        if row.provider in providers
    }

    diversity_values = [
        float(row.normalized_value)
        if row.normalized_value is not None
        else float(row.value)
        for row in observations
        if row.metric == SocialMetric.CREATOR_DIVERSITY
        and row.provider in providers
    ]
    creator_diversity = (
        sum(diversity_values) / len(diversity_values)
        if diversity_values
        else None
    )

    return SocialFeatureSummary(
        provider_count=len(providers),
        evidence_count=len(evidence),
        metric_count=len(metrics),
        source_diversity=source_diversity(signals),
        cross_platform_confirmation=cross_source_confirmation(signals),
        creator_diversity=(
            clamp(creator_diversity)
            if creator_diversity is not None
            else None
        ),
    )
