"""Deterministic feature engine for time-series and cross-source confirmation."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Sequence

from core import MetricSnapshot, Signal, clamp


def values(series: Sequence[MetricSnapshot]) -> list[float]:
    return [float(item.value) for item in sorted(series, key=lambda row: row.collected_at)]


def delta(series: Sequence[MetricSnapshot]) -> float:
    xs = values(series)
    return xs[-1] - xs[-2] if len(xs) >= 2 else 0.0


def velocity(series: Sequence[MetricSnapshot]) -> float:
    rows = sorted(series, key=lambda row: row.collected_at)
    if len(rows) < 2:
        return 0.0
    seconds = (rows[-1].collected_at - rows[-2].collected_at).total_seconds()
    if seconds <= 0:
        return 0.0
    return (rows[-1].value - rows[-2].value) / (seconds / 86400.0)


def acceleration(series: Sequence[MetricSnapshot]) -> float:
    rows = sorted(series, key=lambda row: row.collected_at)
    if len(rows) < 3:
        return 0.0

    def segment(a: MetricSnapshot, b: MetricSnapshot) -> float:
        days = (b.collected_at - a.collected_at).total_seconds() / 86400.0
        return 0.0 if days <= 0 else (b.value - a.value) / days

    prior = segment(rows[-3], rows[-2])
    latest = segment(rows[-2], rows[-1])
    return latest - prior


def moving_average(series: Sequence[MetricSnapshot], points: int = 7) -> float:
    xs = values(series)
    if not xs:
        return 0.0
    tail = xs[-max(1, points):]
    return sum(tail) / len(tail)


def z_score(series: Sequence[MetricSnapshot]) -> float:
    xs = values(series)
    if len(xs) < 3:
        return 0.0
    baseline = xs[:-1]
    if len(set(baseline)) <= 1:
        return 0.0 if xs[-1] == baseline[-1] else (3.0 if xs[-1] > baseline[-1] else -3.0)
    mean = statistics.mean(baseline)
    sd = statistics.pstdev(baseline)
    return 0.0 if sd == 0 else (xs[-1] - mean) / sd


def persistence(series: Sequence[MetricSnapshot], lookback: int = 5) -> float:
    xs = values(series)
    if len(xs) < 2:
        return 0.0
    deltas = [b - a for a, b in zip(xs, xs[1:])][-lookback:]
    if not deltas:
        return 0.0
    positive = sum(1 for item in deltas if item > 0)
    flat = sum(1 for item in deltas if item == 0)
    return 100.0 * (positive + 0.25 * flat) / len(deltas)


def source_diversity(signals: Iterable[Signal]) -> float:
    providers = {signal.provider for signal in signals if signal.normalized_value > 0}
    # Four independent providers is enough to saturate Phase-1 confirmation.
    return clamp(len(providers) / 4.0 * 100.0)


def cross_source_confirmation(signals: Iterable[Signal], threshold: float = 50.0) -> float:
    """Confirm a trend only from materially positive provider signals.

    Source diversity answers "how many providers have evidence".
    Cross-source confirmation answers "how many providers independently show
    meaningful signal strength". Neutral/weak values below threshold do not
    count as trend confirmation.
    """
    rows = [signal for signal in signals if signal.normalized_value >= threshold]
    if not rows:
        return 0.0
    by_provider: dict[str, list[Signal]] = {}
    for row in rows:
        by_provider.setdefault(row.provider, []).append(row)

    provider_strengths = [
        max(signal.normalized_value * signal.confidence / 100.0 for signal in group)
        for group in by_provider.values()
    ]
    breadth = min(1.0, len(provider_strengths) / 4.0)
    mean_strength = sum(provider_strengths) / len(provider_strengths)
    return clamp(mean_strength * (0.55 + 0.45 * breadth))


@dataclass(slots=True)
class FeatureSummary:
    source_diversity: float
    cross_source_confirmation: float
    positive_provider_count: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "source_diversity": round(self.source_diversity, 2),
            "cross_source_confirmation": round(self.cross_source_confirmation, 2),
            "positive_provider_count": self.positive_provider_count,
        }


def summarize_signals(signals: Sequence[Signal]) -> FeatureSummary:
    positive = {row.provider for row in signals if row.normalized_value >= 50.0}
    return FeatureSummary(
        source_diversity=source_diversity(signals),
        cross_source_confirmation=cross_source_confirmation(signals),
        positive_provider_count=len(positive),
    )
