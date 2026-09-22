from __future__ import annotations

import unittest

from wechat_handoff import build_wechat_research_pack


def radar_pack():
    return {
        "schema_version": "research-pack-v1",
        "generated_at": "2026-09-22T14:00:00+00:00",
        "topic": "agent memory",
        "topic_id": "topic:agent-memory",
        "rank_score": 82.0,
        "opportunity": {
            "score": 84.0,
            "reasons": ["Confirmed across 4 independent providers."],
            "components": {"demand": 80, "momentum": 75},
        },
        "coverage": {
            "missing_dimensions": ["supply_gap"],
        },
        "traceability": {
            "citation_coverage": 1.0,
        },
        "guardrails": {
            "facts_require_citation_ids": True,
        },
        "key_questions": [
            {"text": "How should long-term memory be evaluated?", "citation_ids": ["C001"]},
        ],
        "citations": [
            {
                "citation_id": "C001",
                "evidence_id": "gh-1",
                "provider": "github",
                "source": "github.com",
                "source_group": "community",
                "evidence_class": "community_direct",
                "title": "Agent memory evaluation issue",
                "url": "https://github.com/example/repo/issues/1",
                "published_at": "2026-09-20T00:00:00+00:00",
                "retrieved_at": "2026-09-22T00:00:00+00:00",
                "acquisition_method": "official_api",
                "provenance": {"endpoint": "GET /search/issues"},
            },
            {
                "citation_id": "C002",
                "evidence_id": "kp-1",
                "provider": "keyword_planner",
                "source": "googleads.googleapis.com",
                "source_group": "market",
                "evidence_class": "platform_market_metric",
                "title": "agent memory",
                "url": None,
                "published_at": None,
                "retrieved_at": "2026-09-22T00:00:00+00:00",
                "acquisition_method": "oauth_api",
                "provenance": {
                    "endpoint": "KeywordPlanIdeaService.GenerateKeywordHistoricalMetrics"
                },
            },
        ],
        "stats": [
            {
                "metric": "comments",
                "value": 21,
                "subject": "Agent memory evaluation issue",
                "provider": "github",
                "citation_ids": ["C001"],
            },
            {
                "metric": "avg_monthly_searches",
                "value": 1200,
                "subject": "agent memory",
                "provider": "keyword_planner",
                "citation_ids": ["C002"],
            },
            {
                "metric": "untraceable_metric",
                "value": 5,
                "subject": "missing",
                "provider": "unknown",
                "citation_ids": ["C999"],
            },
        ],
    }


class WeChatHandoffTests(unittest.TestCase):
    def test_requires_explicit_audience_and_objective(self):
        with self.assertRaises(ValueError):
            build_wechat_research_pack(
                radar_pack(),
                audience="",
                objective="Explain the opportunity",
            )
        with self.assertRaises(ValueError):
            build_wechat_research_pack(
                radar_pack(),
                audience="AI builders",
                objective="",
            )

    def test_matches_required_wechat_research_pack_shape(self):
        result = build_wechat_research_pack(
            radar_pack(),
            audience="AI builders",
            objective="Explain why agent memory matters now",
        )
        for field in (
            "topic",
            "as_of",
            "audience",
            "objective",
            "sources",
            "claims",
            "insights",
            "open_questions",
        ):
            self.assertIn(field, result)
        self.assertEqual(result["topic"], "agent memory")
        self.assertEqual(result["as_of"], "2026-09-22")

    def test_claims_reference_known_sources_only(self):
        result = build_wechat_research_pack(
            radar_pack(),
            audience="AI builders",
            objective="Explain the opportunity",
        )
        source_ids = {source["id"] for source in result["sources"]}
        self.assertEqual(len(result["claims"]), 2)
        for claim in result["claims"]:
            self.assertTrue(claim["source_ids"])
            self.assertTrue(set(claim["source_ids"]) <= source_ids)
            self.assertEqual(claim["status"], "verified")
        self.assertFalse(
            any("untraceable_metric" in claim["claim"] for claim in result["claims"])
        )

    def test_evidence_classes_map_conservatively(self):
        result = build_wechat_research_pack(
            radar_pack(),
            audience="AI builders",
            objective="Explain the opportunity",
        )
        by_provider = {source["radar_provider"]: source for source in result["sources"]}
        self.assertEqual(by_provider["github"]["source_type"], "primary")
        self.assertEqual(by_provider["github"]["credibility"], "medium")
        self.assertEqual(by_provider["keyword_planner"]["source_type"], "primary")
        self.assertEqual(by_provider["keyword_planner"]["credibility"], "high")

    def test_open_questions_preserve_questions_and_evidence_gaps(self):
        result = build_wechat_research_pack(
            radar_pack(),
            audience="AI builders",
            objective="Explain the opportunity",
        )
        joined = "\n".join(result["open_questions"])
        self.assertIn("How should long-term memory be evaluated?", joined)
        self.assertIn("supply_gap", joined)

    def test_radar_interpretation_is_not_converted_into_verified_claim(self):
        result = build_wechat_research_pack(
            radar_pack(),
            audience="AI builders",
            objective="Explain the opportunity",
        )
        self.assertTrue(result["insights"])
        self.assertTrue(result["insights"][0].startswith("[Radar interpretation]"))
        claim_text = "\n".join(claim["claim"] for claim in result["claims"])
        self.assertNotIn("Confirmed across", claim_text)

    def test_handoff_metadata_preserves_guardrails(self):
        result = build_wechat_research_pack(
            radar_pack(),
            audience="AI builders",
            objective="Explain the opportunity",
        )
        handoff = result["radar_handoff"]
        self.assertEqual(handoff["handoff_type"], "radar_seed_research_pack")
        self.assertTrue(handoff["guardrails"]["facts_require_citation_ids"])
        self.assertEqual(handoff["topic_id"], "topic:agent-memory")


if __name__ == "__main__":
    unittest.main()
