from __future__ import annotations

import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from core import (
    AcquisitionMethod,
    CollectionRequest,
    CollectionResult,
    DataProvider,
    ProviderState,
    Provenance,
    RawEvent,
    stable_id,
    utcnow,
)
from keyword_planner import (
    KeywordPlannerProvider,
    aggregate_keyword_planner_signals,
    commercial_intent_score,
    demand_baseline_score,
)
from pipeline import run_pipeline


class CaptureTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers, payload):
        self.calls.append((url, dict(headers), dict(payload)))
        return self.response


def sample_response():
    return {
        "results": [
            {
                "text": "agent memory architecture",
                "closeVariants": ["ai agent memory architecture"],
                "keywordMetrics": {
                    "avgMonthlySearches": "1200",
                    "competition": "MEDIUM",
                    "competitionIndex": "57",
                    "lowTopOfPageBidMicros": "1500000",
                    "highTopOfPageBidMicros": "7200000",
                    "averageCpcMicros": "3100000",
                    "monthlySearchVolumes": [
                        {"year": "2026", "month": "AUGUST", "monthlySearches": "1400"}
                    ],
                },
            }
        ]
    }


class FakeKeywordPlannerProvider(DataProvider):
    id = "keyword_planner"
    source = "googleads.googleapis.com"

    def collect(self, request):
        now = utcnow()
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=[
                RawEvent(
                    id="kp-1",
                    provider=self.id,
                    source=self.source,
                    acquisition_method=AcquisitionMethod.OAUTH_API,
                    retrieved_at=now,
                    title=request.topic,
                    text=request.topic,
                    metrics={
                        "avg_monthly_searches": 1200.0,
                        "competition_index": 70.0,
                        "high_top_of_page_bid_micros": 9_000_000.0,
                        "low_top_of_page_bid_micros": 2_000_000.0,
                    },
                    raw={"competition": "HIGH"},
                    provenance=Provenance(
                        "official_api_monthly_keyword_research",
                        api_version="v25",
                        endpoint="KeywordPlanIdeaService.GenerateKeywordHistoricalMetrics",
                    ),
                )
            ],
        )


class KeywordPlannerTests(unittest.TestCase):
    def request(self, **metadata):
        return CollectionRequest(
            topic="agent memory architecture",
            metadata=metadata,
        )

    def test_missing_customer_is_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            result = KeywordPlannerProvider(
                CaptureTransport(sample_response())
            ).collect(self.request(access_token="token"))
        self.assertEqual(result.status, ProviderState.DISABLED)
        self.assertEqual(result.events, [])

    def test_missing_token_requires_auth(self):
        with patch.dict(os.environ, {}, clear=True):
            result = KeywordPlannerProvider(
                CaptureTransport(sample_response())
            ).collect(self.request(customer_id="123-456-7890"))
        self.assertEqual(result.status, ProviderState.AUTH_REQUIRED)
        self.assertIn("adwords", result.warnings[0])

    def test_official_request_shape_and_no_developer_token_required(self):
        transport = CaptureTransport(sample_response())
        with patch.dict(os.environ, {}, clear=True):
            result = KeywordPlannerProvider(transport).collect(
                self.request(
                    customer_id="123-456-7890",
                    access_token="oauth-token",
                    login_customer_id="111-222-3333",
                    geo_target="2840",
                    language_constant="1000",
                )
            )

        self.assertEqual(result.status, ProviderState.HEALTHY)
        self.assertEqual(len(transport.calls), 1)
        url, headers, payload = transport.calls[0]
        self.assertEqual(
            url,
            "https://googleads.googleapis.com/v25/customers/1234567890:generateKeywordHistoricalMetrics",
        )
        self.assertEqual(headers["Authorization"], "Bearer oauth-token")
        self.assertEqual(headers["login-customer-id"], "1112223333")
        self.assertNotIn("developer-token", headers)
        self.assertEqual(payload["keywords"], ["agent memory architecture"])
        self.assertEqual(payload["geoTargetConstants"], ["geoTargetConstants/2840"])
        self.assertEqual(payload["language"], "languageConstants/1000")
        self.assertEqual(payload["keywordPlanNetwork"], "GOOGLE_SEARCH")

    def test_metrics_are_parsed_and_provenance_kept(self):
        transport = CaptureTransport(sample_response())
        result = KeywordPlannerProvider(transport).collect(
            self.request(
                customer_id="1234567890",
                access_token="token",
            )
        )
        event = result.events[0]
        self.assertEqual(event.metrics["avg_monthly_searches"], 1200.0)
        self.assertEqual(event.metrics["competition_index"], 57.0)
        self.assertEqual(event.metrics["high_top_of_page_bid_micros"], 7_200_000.0)
        self.assertEqual(event.metrics["average_cpc_micros"], 3_100_000.0)
        self.assertEqual(event.raw["competition"], "MEDIUM")
        self.assertEqual(event.provenance.api_version, "v25")
        self.assertIn("HistoricalMetrics", event.provenance.endpoint)

    def test_http_429_is_rate_limited(self):
        def transport(url, headers, payload):
            raise urllib.error.HTTPError(
                url,
                429,
                "Too Many Requests",
                hdrs=None,
                fp=None,
            )

        result = KeywordPlannerProvider(transport).collect(
            self.request(
                customer_id="1234567890",
                access_token="token",
            )
        )
        self.assertEqual(result.status, ProviderState.RATE_LIMITED)

    def test_demand_score_is_monotonic_and_log_scaled(self):
        values = [
            demand_baseline_score(10),
            demand_baseline_score(100),
            demand_baseline_score(1000),
            demand_baseline_score(10000),
        ]
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1], values[0])
        self.assertLess(values[-1] - values[-2], 25)

    def test_commercial_score_rises_with_bid_and_competition(self):
        low = commercial_intent_score(
            competition_index=20,
            competition="LOW",
            high_top_of_page_bid_micros=500_000,
        )
        high = commercial_intent_score(
            competition_index=90,
            competition="HIGH",
            high_top_of_page_bid_micros=20_000_000,
        )
        self.assertGreater(high, low)

    def test_aggregate_emits_demand_and_commercial_only(self):
        provider = KeywordPlannerProvider(CaptureTransport(sample_response()))
        result = provider.collect(
            self.request(customer_id="1234567890", access_token="token")
        )
        signals = aggregate_keyword_planner_signals(
            result.events,
            topic_id="topic:agent-memory-architecture",
            query_terms=["agent memory"],
        )
        self.assertEqual(
            {signal.signal_type for signal in signals},
            {"demand", "commercial"},
        )
        self.assertNotIn("momentum", {signal.signal_type for signal in signals})
        self.assertTrue(all(signal.evidence_ids for signal in signals))

    def test_pipeline_uses_commercial_signal_without_generic_momentum(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_pipeline(
                topic="agent memory architecture",
                providers=[FakeKeywordPlannerProvider()],
                google_ads_customer_id="1234567890",
                google_ads_access_token="token",
                snapshot_path=str(Path(tmp) / "snapshots.jsonl"),
                cache_path=str(Path(tmp) / "cache.json"),
            )

        signals = report["signals"]
        types = {row["signal_type"] for row in signals}
        self.assertIn("demand", types)
        self.assertIn("commercial", types)
        self.assertNotIn("momentum", types)
        self.assertGreater(
            report["opportunity"]["components"]["business_fit"],
            50.0,
        )
        self.assertEqual(
            report["providers"]["keyword_planner"]["status"],
            "healthy",
        )


if __name__ == "__main__":
    unittest.main()
