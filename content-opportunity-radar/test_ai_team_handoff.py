from __future__ import annotations

import unittest

from ai_team_handoff import build_handoff, render_markdown


class AiTeamHandoffTests(unittest.TestCase):
    def test_build_handoff_preserves_scores_citations_and_worker_contract(self):
        opportunity = {
            "run_id": "run-radar-1",
            "scope": "AI Agents",
            "topic_id": "topic:agent-skills",
            "topic": "agent skills",
            "rank": 1,
            "scores": {"discovery": 53.93, "opportunity": 54.10, "rank": 54.10},
            "confidence": 52.48,
            "freshness": 55.2,
            "reasons": ["Confirmed across 3 independent providers."],
            "source_summary": [{"provider": "github", "evidence_count": 2}],
            "created_at": "2026-10-04T13:06:49+00:00",
        }
        pack = {
            "schema_version": "research-pack-v1",
            "topic": "agent skills",
            "citations": [
                {
                    "citation_id": "C001",
                    "provider": "github",
                    "title": "anthropics/skills",
                    "url": "https://github.com/anthropics/skills",
                }
            ],
            "coverage": {"missing_dimensions": ["supply_gap"]},
            "guardrails": {"facts_require_citation_ids": True},
        }

        handoff = build_handoff(opportunity, pack)

        self.assertEqual(handoff["schema_version"], "radar-ai-team-handoff-v1")
        self.assertEqual(handoff["source"]["radar_run_id"], "run-radar-1")
        self.assertEqual(handoff["decision_context"]["rank"], 1)
        self.assertEqual(handoff["research_pack"]["citations"][0]["citation_id"], "C001")
        self.assertTrue(handoff["guardrails"]["facts_require_citation_ids"])
        self.assertIn("不要要求用户先掌握", handoff["ai_team_contract"]["handoff_goal"])
        self.assertEqual(handoff["deliverable_contract"]["version"], "content-brief-v1")
        self.assertEqual(
            handoff["deliverable_contract"]["artifact_path"],
            ".radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md",
        )
        self.assertEqual(
            handoff["deliverable_contract"]["required_sections"],
            [
                "目标受众",
                "核心内容角度",
                "建议标题",
                "内容大纲",
                "草稿开篇",
                "Evidence Gaps（证据缺口）",
                "Go / No-Go 建议",
            ],
        )
        rendered = render_markdown(handoff)
        self.assertIn("[C001] anthropics/skills", rendered)
        self.assertIn("supply_gap", rendered)
        self.assertIn("## Deliverable contract（交付物合同）", rendered)
        self.assertIn("Go / No-Go 建议", rendered)

    def test_handoff_refuses_pack_without_traceable_citation(self):
        opportunity = {
            "run_id": "r",
            "scope": "AI",
            "topic_id": "topic:x",
            "topic": "x",
        }
        with self.assertRaises(ValueError):
            build_handoff(
                opportunity,
                {"schema_version": "research-pack-v1", "citations": []},
            )


if __name__ == "__main__":
    unittest.main()
