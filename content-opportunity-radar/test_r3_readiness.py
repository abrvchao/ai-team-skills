from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from r3_readiness import build_readiness, render_markdown
from workspace import WorkspaceStore


class R3ReadinessTests(unittest.TestCase):
    def test_preparation_can_be_complete_while_all_real_gates_are_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r3"
            (root / "human-label-pack").mkdir(parents=True)
            (root / "design-partner").mkdir(parents=True)
            (root / "seed-compare").mkdir(parents=True)
            db = str(Path(tmp) / "radar.db")
            with WorkspaceStore(db):
                pass

            batch = {
                "schema_version": "r3-validation-batch-v1",
                "gate": {"minimum_labeled_opportunities": 20, "minimum_unique_labeled_topics": 20, "k": 5, "minimum_precision_at_k": 0.6},
                "queues": [{
                    "scope": "AI Agents", "workspace_id": None, "run_id": "r1", "source_path": None,
                    "document": {"schema_version": "candidate-eval-v1", "scope": "AI Agents", "run_id": "r1", "items": [
                        {"rank": i, "topic_id": f"n-{i}", "topic": f"native {i}", "relevant": None, "note": ""}
                        for i in range(1, 6)
                    ]},
                }],
            }
            (root / "human-label-pack" / "batch.json").write_text(json.dumps(batch), encoding="utf-8")
            session = {"schema_version":"r3-user-session-v1","participant_alias":"p1","scope":"AI Agents","run_id":"r1","items":[{"topic":"x","usefulness_1_to_5":None,"actionable":None,"would_act":None}]}
            (root / "design-partner" / "session.json").write_text(json.dumps(session), encoding="utf-8")
            experimental = {"schema_version":"candidate-eval-v1","scope":"AI Agents","run_id":"e1","items":[{"rank":i,"topic_id":f"e-{i}","topic":f"exp {i}","relevant":None,"note":""} for i in range(1,6)]}
            (root / "seed-compare" / "aihot-experimental.json").write_text(json.dumps(experimental), encoding="utf-8")

            readiness = build_readiness(database=db, r3_root=root, environ={})
            self.assertTrue(readiness["preparation_complete"])
            self.assertEqual(readiness["stage_progress"], "0/4")
            self.assertFalse(readiness["r3_complete"])
            self.assertTrue(all(not g["passed"] for g in readiness["gates"]))

    def test_gsc_readiness_never_exposes_secret_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r3"
            root.mkdir(parents=True)
            db = str(Path(tmp) / "radar.db")
            with WorkspaceStore(db) as store:
                store.create(name="Example", scope="AI", domain="https://example.com", gsc_site_url="sc-domain:example.com")
            readiness = build_readiness(database=db, r3_root=root, environ={"GSC_ACCESS_TOKEN":"secret-never-print"})
            blob = json.dumps(readiness)
            self.assertNotIn("secret-never-print", blob)
            gate = next(g for g in readiness["gates"] if g["id"] == "gsc_first_party")
            self.assertTrue(gate["runtime_token_present"])
            self.assertFalse(gate["credential_value_exposed"])

    def test_forged_gsc_pass_cannot_bypass_workspace_or_token(self):
        # 中文说明：合成的 passed:true 报告不能绕过工作区与令牌门禁。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r3"
            (root / "gsc").mkdir(parents=True)
            (root / "gsc" / "result.json").write_text(
                json.dumps({"passed": True}), encoding="utf-8")
            db = str(Path(tmp) / "radar.db")
            with WorkspaceStore(db):
                pass
            for env in ({}, {"GSC_ACCESS_TOKEN": "synthetic-token"}):
                gate = next(g for g in build_readiness(
                    database=db, r3_root=root, environ=env)["gates"]
                    if g["id"] == "gsc_first_party")
                self.assertFalse(gate["passed"])
            with WorkspaceStore(db) as store:
                store.create(name="Example", scope="AI", domain="https://example.com",
                             gsc_site_url="sc-domain:example.com")
            gate = next(g for g in build_readiness(
                database=db, r3_root=root, environ={})["gates"]
                if g["id"] == "gsc_first_party")
            self.assertFalse(gate["passed"])

    def test_markdown_states_truth_rule(self):
        report = render_markdown({"preparation_complete": True, "stage_progress":"0/4", "stage_percent":0, "r3_complete":False, "gates":[], "next_actions":[]})
        self.assertIn("准备就绪不等于真实验收通过", report)


if __name__ == "__main__":
    unittest.main()
