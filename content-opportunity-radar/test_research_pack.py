from __future__ import annotations

import unittest

from research_pack import build_research_pack, render_markdown


def sample_report():
    return {
        "topic": "agent memory",
        "topic_id": "topic:agent-memory",
        "rank_score": 81.5,
        "opportunity": {
            "score": 84.0,
            "components": {
                "demand": 80,
                "momentum": 76,
                "supply_gap": 68,
                "authority_fit": 72,
                "business_fit": 74,
                "freshness": 90,
                "confidence": 82,
            },
            "reasons": ["Confirmed across providers."],
            "features": {"source_diversity": 75},
            "evidence_ids": ["gh-1", "hn-1", "kp-1", "missing-1"],
        },
        "signals": [
            {
                "signal_type": "demand",
                "provider": "hackernews",
                "evidence_ids": ["hn-1"],
            },
            {
                "signal_type": "momentum",
                "provider": "github",
                "evidence_ids": ["gh-1"],
            },
            {
                "signal_type": "authority",
                "provider": "gsc",
                "evidence_ids": ["gsc-1"],
            },
            {
                "signal_type": "commercial",
                "provider": "keyword_planner",
                "evidence_ids": ["kp-1"],
            },
        ],
        "events": [
            {
                "id": "gh-1",
                "provider": "github",
                "source": "github.com",
                "acquisition_method": "official_api",
                "title": "Agent memory fails after restart — need persistence support",
                "text": "How should an agent restore memory after restart?",
                "url": "https://github.com/example/repo/issues/1",
                "published_at": "2026-09-20T00:00:00+00:00",
                "retrieved_at": "2026-09-22T00:00:00+00:00",
                "metrics": {"comments": 18, "reactions": 7},
                "provenance": {
                    "terms_class": "official_api",
                    "endpoint": "GET /search/issues",
                },
            },
            {
                "id": "hn-1",
                "provider": "hackernews",
                "source": "news.ycombinator.com",
                "acquisition_method": "public_web_api",
                "title": "How do you evaluate long-term agent memory?",
                "text": "",
                "url": "https://news.ycombinator.com/item?id=1",
                "published_at": "2026-09-21T00:00:00+00:00",
                "retrieved_at": "2026-09-22T00:00:00+00:00",
                "metrics": {"points": 55, "comments": 24},
                "provenance": {
                    "terms_class": "public_api",
                    "endpoint": "HN Algolia",
                },
            },
            {
                "id": "kp-1",
                "provider": "keyword_planner",
                "source": "googleads.googleapis.com",
                "acquisition_method": "oauth_api",
                "title": "agent memory",
                "text": "agent memory",
                "url": None,
                "published_at": None,
                "retrieved_at": "2026-09-22T00:00:00+00:00",
                "metrics": {
                    "avg_monthly_searches": 1200,
                    "competition_index": 61,
                    "high_top_of_page_bid_micros": 8000000,
                },
                "provenance": {
                    "terms_class": "official_api_monthly_keyword_research",
                    "endpoint": "KeywordPlanIdeaService.GenerateKeywordHistoricalMetrics",
                },
            },
            {
                "id": "unused",
                "provider": "google_news",
                "source": "news.google.com",
                "acquisition_method": "rss",
                "title": "Unused evidence",
                "text": "",
                "url": "https://example.com/unused",
                "metrics": {"mention": 1},
                "provenance": {"terms_class": "rss"},
            },
        ],
    }


class ResearchPackTests(unittest.TestCase):
    def test_only_opportunity_evidence_becomes_citations(self):
        pack = build_research_pack(sample_report())
        evidence_ids = {row["evidence_id"] for row in pack["citations"]}
        self.assertEqual(evidence_ids, {"gh-1", "hn-1", "kp-1"})
        self.assertNotIn("unused", evidence_ids)
        self.assertEqual(pack["traceability"]["unresolved_evidence_ids"], ["missing-1"])
        self.assertEqual(pack["traceability"]["uncited_due_to_limit_ids"], [])
        self.assertEqual(pack["traceability"]["requested_evidence_count"], 4)
        self.assertEqual(pack["traceability"]["resolved_evidence_count"], 3)
        self.assertEqual(pack["traceability"]["cited_evidence_count"], 3)

    def test_questions_and_pain_are_cited(self):
        pack = build_research_pack(sample_report())
        self.assertTrue(pack["key_questions"])
        self.assertTrue(pack["pain_signals"])
        for row in [*pack["key_questions"], *pack["pain_signals"]]:
            self.assertTrue(row["citation_ids"])
            self.assertTrue(all(cid.startswith("C") for cid in row["citation_ids"]))

    def test_stats_are_directly_traceable(self):
        pack = build_research_pack(sample_report())
        metric_names = {row["metric"] for row in pack["stats"]}
        self.assertIn("avg_monthly_searches", metric_names)
        self.assertIn("comments", metric_names)
        self.assertTrue(all(row["citation_ids"] for row in pack["stats"]))

    def test_dimension_coverage_does_not_invent_supply(self):
        pack = build_research_pack(sample_report())
        coverage = pack["coverage"]["dimensions"]
        self.assertTrue(coverage["demand"]["observed"])
        self.assertTrue(coverage["momentum"]["observed"])
        self.assertTrue(coverage["authority_fit"]["observed"])
        self.assertTrue(coverage["business_fit"]["observed"])
        self.assertFalse(coverage["supply_gap"]["observed"])
        self.assertIn("supply_gap", pack["coverage"]["missing_dimensions"])
        self.assertEqual(pack["coverage"]["research_readiness"], 0.8)

    def test_source_groups_distinguish_market_and_community(self):
        pack = build_research_pack(sample_report())
        groups = pack["source_summary"]["source_groups"]
        self.assertEqual(groups["market"], 1)
        self.assertEqual(groups["community"], 2)
        classes = {row["evidence_id"]: row["evidence_class"] for row in pack["citations"]}
        self.assertEqual(classes["kp-1"], "platform_market_metric")
        self.assertEqual(classes["gh-1"], "community_direct")

    def test_citation_limit_is_not_reported_as_unresolved(self):
        report = sample_report()
        report["opportunity"]["evidence_ids"] = ["gh-1", "hn-1", "kp-1"]
        pack = build_research_pack(report, max_citations=2)
        self.assertEqual(pack["traceability"]["unresolved_evidence_ids"], [])
        self.assertEqual(pack["traceability"]["resolved_evidence_count"], 3)
        self.assertEqual(pack["traceability"]["cited_evidence_count"], 2)
        self.assertEqual(len(pack["traceability"]["uncited_due_to_limit_ids"]), 1)

    def test_guardrails_are_explicit(self):
        pack = build_research_pack(sample_report())
        self.assertFalse(pack["guardrails"]["trend_score_generated_by_llm"])
        self.assertFalse(pack["guardrails"]["topic_generated_by_llm"])
        self.assertTrue(pack["guardrails"]["facts_require_citation_ids"])
        self.assertTrue(pack["guardrails"]["missing_dimensions_must_not_be_invented"])

    def test_markdown_uses_citation_ids_and_evidence_gaps(self):
        pack = build_research_pack(sample_report())
        markdown = render_markdown(pack)
        self.assertIn("# Research Pack — agent memory", markdown)
        self.assertIn("[C001]", markdown)
        self.assertIn("## Evidence gaps", markdown)
        self.assertIn("supply_gap", markdown)


if __name__ == "__main__":
    unittest.main()
