from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone

from core import CollectionRequest, ProviderState, RawEvent
from gsc import GSCProvider, build_gsc_signals, summarize_gsc_queries


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, payload):
        self.calls.append({
            "url": url,
            "headers": dict(headers),
            "payload": dict(payload),
        })
        if not self.responses:
            return {"rows": []}
        return self.responses.pop(0)


class GSCTests(unittest.TestCase):
    def test_missing_token_returns_auth_required(self):
        provider = GSCProvider(transport=FakeTransport([]))
        result = provider.collect(
            CollectionRequest(
                topic="AI Agents",
                metadata={"site_url": "sc-domain:example.com"},
            )
        )
        self.assertEqual(result.status, ProviderState.AUTH_REQUIRED)
        self.assertEqual(result.events, [])

    def test_paginates_with_start_row_and_parses_metrics(self):
        transport = FakeTransport([
            {
                "responseAggregationType": "byPage",
                "rows": [
                    {
                        "keys": ["2026-09-01", "ai agents", "https://example.com/agents"],
                        "clicks": 2,
                        "impressions": 20,
                        "ctr": 0.1,
                        "position": 12.0,
                    },
                    {
                        "keys": ["2026-09-01", "agent memory", "https://example.com/memory"],
                        "clicks": 1,
                        "impressions": 10,
                        "ctr": 0.1,
                        "position": 18.0,
                    },
                ],
            },
            {
                "responseAggregationType": "byPage",
                "rows": [
                    {
                        "keys": ["2026-09-02", "ai agents", "https://example.com/agents"],
                        "clicks": 3,
                        "impressions": 30,
                        "ctr": 0.1,
                        "position": 10.0,
                    }
                ],
            },
        ])
        provider = GSCProvider(transport=transport)
        result = provider.collect(
            CollectionRequest(
                topic="AI Agents",
                metadata={
                    "site_url": "sc-domain:example.com",
                    "access_token": "super-secret-token",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-02",
                    "row_limit": 2,
                    "max_rows": 4,
                },
            )
        )

        self.assertEqual(result.status, ProviderState.HEALTHY)
        self.assertEqual(len(result.events), 3)
        self.assertEqual([call["payload"]["startRow"] for call in transport.calls], [0, 2])
        self.assertEqual(result.events[0].metrics["impressions"], 20.0)
        self.assertEqual(result.events[0].metrics["position"], 12.0)
        self.assertEqual(result.events[0].raw["completeness"], "top_rows_only_not_exhaustive")
        self.assertTrue(any("not exhaustive" in warning for warning in result.warnings))

    def test_oauth_token_is_never_persisted_in_event_payload(self):
        transport = FakeTransport([{
            "rows": [{
                "keys": ["2026-09-01", "ai agents", "https://example.com/agents"],
                "clicks": 1,
                "impressions": 10,
                "ctr": 0.1,
                "position": 9.0,
            }]
        }])
        secret = "token-that-must-not-be-stored"
        result = GSCProvider(transport=transport).collect(
            CollectionRequest(
                topic="AI Agents",
                metadata={
                    "site_url": "sc-domain:example.com",
                    "access_token": secret,
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-01",
                    "row_limit": 10,
                },
            )
        )
        serialized = json.dumps(
            [event.to_dict(include_raw=True) for event in result.events],
            ensure_ascii=False,
            default=str,
        )
        self.assertNotIn(secret, serialized)
        self.assertNotIn(secret, result.events[0].provenance.endpoint or "")

    def test_35_day_history_creates_demand_momentum_and_authority(self):
        rows: list[RawEvent] = []
        latest = date(2026, 9, 21)
        start = latest - timedelta(days=34)

        def make_event(day: date, impressions: float, clicks: float, position: float) -> RawEvent:
            from core import AcquisitionMethod, Provenance, stable_id
            published = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            event_id = stable_id("gsc-test", day.isoformat())
            return RawEvent(
                id=event_id,
                provider="gsc",
                source="sc-domain:example.com",
                acquisition_method=AcquisitionMethod.OAUTH_API,
                retrieved_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
                external_id=event_id,
                url="https://example.com/agents",
                title="ai agents",
                text="ai agents",
                published_at=published,
                metrics={
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": clicks / impressions if impressions else 0,
                    "position": position,
                },
                raw={
                    "dimensions": {
                        "date": day.isoformat(),
                        "query": "ai agents",
                        "page": "https://example.com/agents",
                    },
                    "completeness": "top_rows_only_not_exhaustive",
                },
                provenance=Provenance("first_party_oauth_top_rows", "v3", "test"),
            )

        for offset in range(35):
            day = start + timedelta(days=offset)
            if day >= latest - timedelta(days=6):
                rows.append(make_event(day, impressions=20, clicks=3, position=15))
            else:
                rows.append(make_event(day, impressions=10, clicks=1, position=20))

        features = summarize_gsc_queries(rows)
        self.assertEqual(len(features), 1)
        feature = features[0]
        self.assertAlmostEqual(feature.recent_impressions_per_day, 20.0)
        self.assertAlmostEqual(feature.baseline_impressions_per_day, 10.0)
        self.assertAlmostEqual(feature.impression_growth_ratio, 1.0)
        self.assertAlmostEqual(feature.position_improvement, 5.0)
        self.assertGreater(feature.demand_score, 0)
        self.assertGreater(feature.momentum_score, 50)
        self.assertEqual(feature.authority_score, 70.0)
        self.assertEqual(feature.confidence, 78.0)

        signals = build_gsc_signals(
            rows,
            topic_id="topic:ai-agents",
            query_contains="ai agents",
        )
        self.assertEqual({signal.signal_type for signal in signals}, {"demand", "momentum", "authority"})
        self.assertTrue(all(signal.provider == "gsc" for signal in signals))
        self.assertTrue(all(signal.evidence_ids for signal in signals))


if __name__ == "__main__":
    unittest.main()
