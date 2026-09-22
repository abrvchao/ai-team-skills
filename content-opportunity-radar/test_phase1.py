from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import (
    AcquisitionMethod,
    CollectionRequest,
    DataProvider,
    MetricSnapshot,
    ProviderRegistry,
    ProviderState,
    Provenance,
    RawEvent,
    Signal,
    SnapshotStore,
)
from features import acceleration, delta, summarize_signals, velocity
from opportunity import score_opportunity
from pipeline import dedupe_news, historical_momentum_signals


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def signal(provider: str, value: float, *, kind: str = "momentum", evidence: str | None = None) -> Signal:
    return Signal(
        id=f"signal-{provider}-{value}",
        topic_id="topic:ai-agents",
        entity_ids=[],
        signal_type=kind,
        source=provider,
        provider=provider,
        observed_at=NOW,
        value=value,
        normalized_value=value,
        confidence=80.0,
        evidence_ids=[evidence or f"e-{provider}"],
        freshness_seconds=60,
    )


class BrokenProvider(DataProvider):
    id = "broken"

    def collect(self, request: CollectionRequest):
        raise RuntimeError("boom")


class Phase1Tests(unittest.TestCase):
    def test_snapshot_store_is_append_only_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.jsonl"
            store = SnapshotStore(path)
            store.append(MetricSnapshot("topic", "t", "event_count", 3, NOW, "github"))
            store.append(MetricSnapshot("topic", "t", "event_count", 5, NOW + timedelta(hours=1), "github"))

            reloaded = SnapshotStore(path)
            rows = reloaded.series("topic", "t", "event_count", "github")
            self.assertEqual([row.value for row in rows], [3.0, 5.0])
            self.assertEqual(reloaded.latest("topic", "t", "event_count", "github").value, 5.0)

    def test_time_series_features_are_deterministic(self):
        series = [
            MetricSnapshot("topic", "t", "event_count", 10, NOW, "github"),
            MetricSnapshot("topic", "t", "event_count", 15, NOW + timedelta(days=1), "github"),
            MetricSnapshot("topic", "t", "event_count", 25, NOW + timedelta(days=2), "github"),
        ]
        self.assertEqual(delta(series), 10.0)
        self.assertEqual(velocity(series), 10.0)
        self.assertEqual(acceleration(series), 5.0)

    def test_cross_source_confirmation_ignores_weak_signals(self):
        summary = summarize_signals([
            signal("github", 49),
            signal("hackernews", 40),
            signal("google_news", 80),
        ])
        self.assertEqual(summary.source_diversity, 75.0)
        self.assertEqual(summary.positive_provider_count, 1)
        self.assertGreater(summary.cross_source_confirmation, 0)
        self.assertLess(summary.cross_source_confirmation, 50)

    def test_provider_failure_is_isolated(self):
        registry = ProviderRegistry()
        provider = BrokenProvider()
        registry.register(provider)
        result = registry.safe_collect(provider, CollectionRequest(topic="AI Agents"))
        self.assertEqual(result.status, ProviderState.FAILED)
        self.assertEqual(result.events, [])
        self.assertIn("RuntimeError", result.warnings[0])

    def test_short_interval_does_not_manufacture_historical_momentum(self):
        store = SnapshotStore()
        store.append(MetricSnapshot("topic", "topic:ai-agents", "event_count", 5, NOW, "github"))
        store.append(MetricSnapshot("topic", "topic:ai-agents", "event_count", 8, NOW + timedelta(seconds=30), "github"))
        rows = historical_momentum_signals(
            store=store,
            topic_id="topic:ai-agents",
            provider_ids=["github"],
        )
        self.assertEqual(rows, [])

    def test_positive_historical_growth_creates_evidence_but_flat_does_not_confirm(self):
        store = SnapshotStore()
        store.append(MetricSnapshot("topic", "topic:ai-agents", "event_count", 5, NOW, "github"))
        store.append(MetricSnapshot("topic", "topic:ai-agents", "event_count", 8, NOW + timedelta(hours=1), "github"))
        store.append(MetricSnapshot("topic", "topic:ai-agents", "event_count", 8, NOW, "hackernews"))
        store.append(MetricSnapshot("topic", "topic:ai-agents", "event_count", 8, NOW + timedelta(hours=1), "hackernews"))

        rows = historical_momentum_signals(
            store=store,
            topic_id="topic:ai-agents",
            provider_ids=["github", "hackernews"],
        )
        by_provider = {row.provider: row for row in rows}
        self.assertGreater(by_provider["github"].normalized_value, 0)
        self.assertEqual(by_provider["hackernews"].normalized_value, 0)
        self.assertEqual(summarize_signals(rows).positive_provider_count, 1)

    def test_news_dedup_keeps_one_near_duplicate(self):
        def event(provider: str, title: str, event_id: str) -> RawEvent:
            return RawEvent(
                id=event_id,
                provider=provider,
                source=provider,
                acquisition_method=AcquisitionMethod.RSS,
                retrieved_at=NOW,
                title=title,
                url=f"https://example.com/{event_id}",
                provenance=Provenance("test"),
            )

        rows = dedupe_news([
            event("google_news", "OpenAI launches new AI agent platform", "a"),
            event("gdelt", "OpenAI launches a new AI agent platform", "b"),
            event("gdelt", "Completely different robotics story", "c"),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.id for row in rows}, {"a", "c"})

    def test_opportunity_score_is_explainable_and_evidence_backed(self):
        rows = [
            signal("github", 75, kind="momentum", evidence="repo-1"),
            signal("hackernews", 80, kind="demand", evidence="hn-1"),
            signal("google_news", 60, kind="media", evidence="news-1"),
        ]
        opportunity = score_opportunity(
            topic_id="topic:ai-agents",
            topic="AI Agents",
            signals=rows,
        )
        self.assertEqual(set(opportunity.components), {
            "demand", "momentum", "supply_gap", "authority_fit",
            "business_fit", "freshness", "confidence",
        })
        self.assertEqual(set(opportunity.evidence_ids), {"repo-1", "hn-1", "news-1"})
        self.assertTrue(0 <= opportunity.score <= 100)
        self.assertEqual(opportunity.components["authority_fit"], 50.0)
        self.assertEqual(opportunity.components["business_fit"], 50.0)


if __name__ == "__main__":
    unittest.main()
