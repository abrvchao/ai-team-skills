from __future__ import annotations

import unittest
from unittest.mock import patch

import radar_lite


class FakeStore:
    def __init__(self) -> None:
        self.items = [
            {
                "run_id": "run-1",
                "scope": "AI Agents",
                "topic_id": "topic:agent-skills",
                "topic": "agent skills",
                "rank": 1,
                "scores": {"opportunity": 54.1},
                "confidence": 52.48,
                "freshness": 55.2,
                "reasons": ["Confirmed across sources.", "Momentum is elevated."],
                "source_summary": [
                    {"provider": "github"},
                    {"provider": "google_news"},
                ],
                "created_at": "2026-10-06T00:00:00+00:00",
            }
        ]

    def list_opportunities(self, **kwargs):
        return {"run": {"run_id": "run-1"}, "items": self.items, "next_cursor": None}

    def research_pack(self, topic_id, **kwargs):
        return {
            "schema_version": "research-pack-v1",
            "topic_id": topic_id,
            "topic": "agent skills",
            "citations": [{"citation_id": "c1", "title": "Evidence"}],
            "coverage": {"missing_dimensions": ["supply_gap"]},
        }


class RadarLiteTests(unittest.TestCase):
    def test_cards_are_small_and_handoff_ready(self):
        payload = radar_lite.build_cards(FakeStore(), scope="AI Agents", limit=5)
        self.assertEqual(payload["schema_version"], "radar-lite-v1")
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(len(payload["cards"]), 1)
        card = payload["cards"][0]
        self.assertTrue(card["handoff_ready"])
        self.assertEqual(card["citation_count"], 1)
        self.assertEqual(card["providers"], ["github", "google_news"])
        self.assertEqual(card["missing_dimensions"], ["supply_gap"])

    def test_handoff_by_rank_reuses_existing_contract(self):
        handoff = radar_lite.handoff_by_rank(FakeStore(), scope="AI Agents", rank=1)
        self.assertEqual(handoff["schema_version"], "radar-ai-team-handoff-v1")
        self.assertEqual(handoff["source"]["topic"], "agent skills")
        self.assertEqual(handoff["decision_context"]["rank"], 1)
        self.assertTrue(handoff["guardrails"]["facts_require_citation_ids"])

    def test_lite_scan_forces_native_bounded_mode(self):
        with patch("radar_lite.run_discovery") as discovery, patch(
            "radar_lite.OpportunityReadStore"
        ) as store_cls, patch("radar_lite.build_cards", return_value={"cards": []}) as cards:
            store_cls.return_value.__enter__.return_value = object()
            radar_lite.run_lite_scan(scope="AI Agents", db="x.db", top=5)
            kwargs = discovery.call_args.kwargs
            self.assertEqual(kwargs["seed_mode"], "native")
            self.assertIsNone(kwargs["gsc_site_url"])
            self.assertIsNone(kwargs["google_ads_customer_id"])
            self.assertFalse(kwargs["hydrate_content"])
            self.assertTrue(kwargs["include_research_pack"])
            cards.assert_called_once()

    def test_markdown_is_user_facing_not_internal_dump(self):
        payload = radar_lite.build_cards(FakeStore(), scope="AI Agents")
        text = radar_lite.render_cards_markdown(payload)
        self.assertIn("#1 agent skills", text)
        self.assertIn("Handoff ready（可交接）: True", text)
        self.assertIn("Gaps（缺口）: supply_gap", text)


if __name__ == "__main__":
    unittest.main()
