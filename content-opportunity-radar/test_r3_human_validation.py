from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from r3_gsc_validation import doctor, validate_result
from r3_user_session import SESSION_SCHEMA, score_session
from workspace import WorkspaceStore


class R3HumanValidationTests(unittest.TestCase):
    def test_gsc_doctor_never_exposes_token_and_requires_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "radar.db")
            with WorkspaceStore(db) as store:
                ws = store.create(name="Example", scope="AI Agents", domain="https://example.com")
            result = doctor(database=db, workspace_id=ws.workspace_id, environ={"GSC_ACCESS_TOKEN": "super-secret"})
            self.assertFalse(result["ready"])
            self.assertFalse(result["checks"]["gsc_property_configured"])
            self.assertTrue(result["checks"]["gsc_access_token_present"])
            self.assertNotIn("super-secret", str(result))

    def test_gsc_doctor_ready_with_property_and_token_presence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "radar.db")
            with WorkspaceStore(db) as store:
                ws = store.create(name="Example", scope="AI Agents", domain="https://example.com", gsc_site_url="sc-domain:example.com")
            result = doctor(database=db, workspace_id=ws.workspace_id, environ={"GSC_ACCESS_TOKEN": "secret"})
            self.assertTrue(result["ready"])
            self.assertFalse(result["credential_value_exposed"])

    def test_gsc_result_rejects_unverified_payload_even_with_opportunities(self):
        payload = {
            "workspace": {"workspace_id": "ws1"},
            "collection": {"jobs": [{"provider": "gsc", "status": "healthy", "event_count": 3, "warnings": []}]},
            "top_opportunities": [{"rank": 1, "topic": "agent memory"}],
        }
        # High 修复：假 JSON 不再因为声称 healthy + 正事件数就通过。
        self.assertFalse(validate_result(payload)["passed"])
        self.assertFalse(validate_result(payload)["provider_evidence_pass"])
        payload["collection"]["jobs"][0]["event_count"] = 0
        self.assertFalse(validate_result(payload)["passed"])

    def test_design_partner_session_is_incomplete_until_human_fields_exist(self):
        session = {
            "schema_version": SESSION_SCHEMA,
            "participant_alias": "p1",
            "scope": "AI Agents",
            "run_id": "r1",
            "items": [{"topic": "agent memory", "usefulness_1_to_5": None, "actionable": None, "would_act": None}],
        }
        self.assertFalse(score_session(session)["completed"])
        session["items"][0].update({"usefulness_1_to_5": 4, "actionable": True, "would_act": True})
        result = score_session(session)
        self.assertTrue(result["completed"])
        self.assertEqual(result["mean_usefulness"], 4)
        self.assertTrue(result["at_least_one_real_action_intent"])


if __name__ == "__main__":
    unittest.main()
