from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core import AcquisitionMethod
from opportunity import score_opportunity
from social import (
    SocialMetric,
    SocialObservation,
    dedupe_social_observations,
    history_percentile_score,
    normalize_social_observations,
    summarize_social_signals,
)


NOW = datetime(2026, 9, 26, 5, 0, tzinfo=timezone.utc)


def obs(
    provider: str,
    metric: SocialMetric,
    value: float,
    *,
    normalized: float | None = None,
    evidence: str | None = None,
    observation_id: str | None = None,
) -> SocialObservation:
    return SocialObservation(
        provider=provider,
        source=f"{provider}.example",
        topic_id="topic:ai-agent",
        metric=metric,
        value=value,
        observed_at=NOW,
        evidence_ids=[evidence or f"{provider}-{metric.value}"],
        acquisition_method=AcquisitionMethod.OFFICIAL_API,
        confidence=90.0,
        normalized_value=normalized,
        observation_id=observation_id,
    )


class ChinaSocialSignalTests(unittest.TestCase):
    def test_missing_metric_stays_unknown_without_history(self):
        result = normalize_social_observations(
            [obs("zhihu", SocialMetric.QUESTION_COUNT, 12)],
            now=NOW,
        )
        self.assertEqual(result.signals, [])
        self.assertEqual(
            result.skipped[0]["reason"],
            "missing_normalization_or_history",
        )

    def test_history_percentile_is_provider_relative_and_rank_is_inverse(self):
        self.assertEqual(
            history_percentile_score(30, [10, 20, 25, 40]),
            75.0,
        )
        self.assertEqual(
            history_percentile_score(
                2,
                [10, 8, 4, 1],
                inverse=True,
            ),
            75.0,
        )
        self.assertIsNone(history_percentile_score(5, [1, 2]))

    def test_exact_duplicate_observation_is_counted_once(self):
        row = obs(
            "weibo",
            SocialMetric.MENTION_COUNT,
            100,
            normalized=70,
            observation_id="same-observation",
        )
        unique = dedupe_social_observations([row, row])
        self.assertEqual(len(unique), 1)

        result = normalize_social_observations(
            [row, row],
            now=NOW,
        )
        self.assertEqual(result.observations_seen, 2)
        self.assertEqual(result.observations_deduped, 1)
        self.assertEqual(len(result.signals), 1)

    def test_metric_mapping_reuses_existing_radar_signal_types(self):
        rows = [
            obs("zhihu", SocialMetric.QUESTION_COUNT, 10, normalized=80),
            obs("xiaohongshu", SocialMetric.PAIN_COUNT, 7, normalized=75),
            obs("weibo", SocialMetric.ENGAGEMENT_VELOCITY, 3, normalized=85),
            obs("bilibili", SocialMetric.NEW_CONTENT_COUNT, 20, normalized=70),
        ]
        result = normalize_social_observations(rows, now=NOW)
        signal_types = {row.signal_type for row in result.signals}
        self.assertEqual(
            signal_types,
            {"question", "pain", "momentum", "supply"},
        )

    def test_creator_diversity_is_diagnostic_not_an_extra_score(self):
        rows = [
            obs("zhihu", SocialMetric.QUESTION_COUNT, 10, normalized=80),
            obs(
                "zhihu",
                SocialMetric.CREATOR_DIVERSITY,
                72,
                normalized=72,
            ),
        ]
        result = normalize_social_observations(rows, now=NOW)
        self.assertEqual(len(result.signals), 1)
        self.assertEqual(
            result.skipped[0]["reason"],
            "diagnostic_metric_not_scored",
        )
        summary = summarize_social_signals(
            result.signals,
            observations=rows,
        )
        self.assertEqual(summary.creator_diversity, 72.0)

    def test_cross_platform_confirmation_counts_providers_not_event_volume(self):
        rows = [
            obs(
                "zhihu",
                SocialMetric.QUESTION_COUNT,
                10 + i,
                normalized=80,
                evidence=f"zhihu-{i}",
                observation_id=f"zhihu-{i}",
            )
            for i in range(10)
        ]
        rows += [
            obs(
                "weibo",
                SocialMetric.MENTION_COUNT,
                30,
                normalized=80,
            ),
            obs(
                "bilibili",
                SocialMetric.ENGAGEMENT_VELOCITY,
                4,
                normalized=80,
            ),
        ]
        result = normalize_social_observations(rows, now=NOW)
        summary = summarize_social_signals(
            result.signals,
            observations=rows,
        )
        self.assertEqual(summary.provider_count, 3)
        self.assertEqual(summary.source_diversity, 75.0)
        self.assertGreater(summary.cross_platform_confirmation, 0.0)

    def test_social_signals_feed_existing_opportunity_score(self):
        rows = [
            obs("zhihu", SocialMetric.QUESTION_COUNT, 12, normalized=82),
            obs("weibo", SocialMetric.MENTION_COUNT, 60, normalized=75),
            obs(
                "bilibili",
                SocialMetric.ENGAGEMENT_VELOCITY,
                5,
                normalized=78,
            ),
            obs(
                "xiaohongshu",
                SocialMetric.CONTENT_SUPPLY,
                15,
                normalized=35,
            ),
        ]
        result = normalize_social_observations(rows, now=NOW)
        opportunity = score_opportunity(
            topic_id="topic:ai-agent",
            topic="AI Agent",
            signals=result.signals,
        )
        self.assertGreater(opportunity.components["demand"], 70.0)
        self.assertGreater(opportunity.components["momentum"], 70.0)
        # Low observed content supply becomes a high supply gap using the
        # existing Opportunity Score logic.
        self.assertEqual(opportunity.components["supply_gap"], 65.0)
        self.assertEqual(
            sorted(opportunity.evidence_ids),
            sorted({eid for row in rows for eid in row.evidence_ids}),
        )


if __name__ == "__main__":
    unittest.main()
