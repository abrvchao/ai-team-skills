from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from product_intake import ProductContractStore
from radar_handoff_intake import draft_from_handoff


class FakeTeam:
    def __init__(self) -> None:
        self.calls = []

    def start_run(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run_handoff_001", "status": "primary_running"}


class RadarHandoffIntakeTests(unittest.TestCase):
    def _write_handoff(self, workspace: Path) -> Path:
        path = workspace / "HANDOFF.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "radar-ai-team-handoff-v1",
                    "source": {
                        "radar_run_id": "radar-1",
                        "scope": "AI Agents",
                        "topic": "agent skills",
                        "rank": 1,
                    },
                    "ai_team_contract": {
                        "handoff_goal": "把 Agent Skills 机会做成可直接审阅的中文内容成果。",
                        "worker_rules": [
                            "先读取 research_pack，再开始分析或实现。",
                            "事实性陈述必须引用 citation_id。",
                        ],
                    },
                    "deliverable_contract": {
                        "version": "content-brief-v1",
                        "artifact_path": ".radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md",
                        "acceptance": [
                            "必须输出目标受众、标题、内容大纲和 Go / No-Go 建议。",
                            "所有事实性陈述必须引用 handoff 中存在的 citation_id。",
                        ],
                    },
                    "guardrails": {"facts_require_citation_ids": True},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_draft_maps_handoff_into_product_contract_and_context_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "product"
            workspace.mkdir()
            handoff = self._write_handoff(workspace)
            store = ProductContractStore(Path(tmp) / "contracts")

            draft = draft_from_handoff(
                handoff,
                workspace=workspace,
                store=store,
                participants=["dsh", "qwen"],
            )

            self.assertEqual(draft["status"], "draft")
            self.assertEqual(draft["context_artifacts"], ["HANDOFF.json"])
            self.assertEqual(
                draft["deliverable_artifacts"],
                [".radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md"],
            )
            self.assertEqual(draft["fallback_policy"], "salvage|degrade")
            self.assertEqual(draft["resource_policy"]["mode"], "stop_loss")
            self.assertEqual(draft["radar_handoff"]["source"]["topic"], "agent skills")
            self.assertTrue(draft["radar_handoff"]["facts_require_citation_ids"])
            self.assertIn("citation_id", " ".join(draft["acceptance"]))

            team = FakeTeam()
            started = store.decide_and_start(
                contract_id=draft["contract_id"],
                approved=True,
                team=team,
            )
            self.assertEqual(started["run_id"], "run_handoff_001")
            self.assertEqual(team.calls[0]["context_artifacts"], ["HANDOFF.json"])
            self.assertEqual(
                team.calls[0]["deliverable_artifacts"],
                [".radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md"],
            )
            self.assertEqual(team.calls[0]["fallback_policy"], "salvage|degrade")

    def test_missing_artifact_path_gets_safe_backward_compatible_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "product"
            workspace.mkdir()
            handoff = self._write_handoff(workspace)
            payload = json.loads(handoff.read_text(encoding="utf-8"))
            payload["deliverable_contract"].pop("artifact_path", None)
            payload["source"]["topic_id"] = "topic:agent-skills"
            handoff.write_text(json.dumps(payload), encoding="utf-8")
            draft = draft_from_handoff(
                handoff,
                workspace=workspace,
                store=ProductContractStore(Path(tmp) / "contracts"),
            )
            self.assertEqual(
                draft["deliverable_artifacts"],
                [".radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md"],
            )

    def test_handoff_outside_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "product"
            workspace.mkdir()
            outside = Path(tmp) / "HANDOFF.json"
            outside.write_text("{}", encoding="utf-8")
            store = ProductContractStore(Path(tmp) / "contracts")
            with self.assertRaises(ValueError):
                draft_from_handoff(outside, workspace=workspace, store=store)

    def test_wrong_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "product"
            workspace.mkdir()
            path = workspace / "HANDOFF.json"
            path.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
            store = ProductContractStore(Path(tmp) / "contracts")
            with self.assertRaises(ValueError):
                draft_from_handoff(path, workspace=workspace, store=store)


if __name__ == "__main__":
    unittest.main()
