from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from api import create_handler
from radar_product import RadarProductActions
from read_model import OpportunityReadStore


class FakeActions:
    def __init__(self) -> None:
        self.scans = []
        self.handoffs = []

    def run_scan(self, payload):
        self.scans.append(payload)
        return {"status": "completed", "cards": [{"rank": 1}]}

    def create_handoff(self, payload):
        self.handoffs.append(payload)
        return {"status": "created", "handoff_path": "handoffs/HANDOFF.json"}


class RadarProductHTTPTests(unittest.TestCase):
    def test_local_scan_and_handoff_actions_are_explicit_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db):
                pass
            actions = FakeActions()
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(str(db), actions=actions),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=5
                )
                body = json.dumps({"scope": "AI Agents", "top": 5})
                conn.request(
                    "POST",
                    "/v1/lite/scan",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["status"], "completed")
                self.assertEqual(actions.scans[0]["scope"], "AI Agents")

                body = json.dumps({"scope": "AI Agents", "rank": 1})
                conn.request(
                    "POST",
                    "/v1/lite/handoff",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["status"], "created")
                self.assertEqual(actions.handoffs[0]["rank"], 1)

                conn.request("POST", "/v1/opportunities", body=b"{}")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 405)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_invalid_json_is_rejected_without_running_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db):
                pass
            actions = FakeActions()
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(str(db), actions=actions),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=5
                )
                conn.request("POST", "/v1/lite/scan", body=b"not-json")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 400)
                self.assertEqual(actions.scans, [])
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class RadarProductActionsTests(unittest.TestCase):
    @patch("radar_product.run_lite_scan")
    def test_scan_reuses_lite_scan_with_bounded_inputs(self, mocked_scan):
        mocked_scan.return_value = {"run_id": "run-1", "cards": [{"rank": 1}]}
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            actions = RadarProductActions(database=db, handoff_root=Path(tmp) / "handoffs")
            result = actions.run_scan({"scope": "AI Agents", "top": 5})
            self.assertEqual(result["status"], "completed")
            kwargs = mocked_scan.call_args.kwargs
            self.assertEqual(kwargs["scope"], "AI Agents")
            self.assertEqual(kwargs["top"], 5)
            self.assertIsNone(kwargs["workspace_id"])

    @patch("radar_product.run_lite_scan")
    def test_scan_language_sets_expected_default_country(self, mocked_scan):
        mocked_scan.return_value = {"run_id": "run-ja", "cards": []}
        with tempfile.TemporaryDirectory() as tmp:
            actions = RadarProductActions(
                database=Path(tmp) / "radar.db",
                handoff_root=Path(tmp) / "handoffs",
            )
            result = actions.run_scan({"scope": "AI Agents", "language": "ja"})
            self.assertEqual(result["language"], "ja")
            self.assertEqual(result["country"], "JP")
            kwargs = mocked_scan.call_args.kwargs
            self.assertEqual(kwargs["language"], "ja")
            self.assertEqual(kwargs["country"], "JP")

    @patch("radar_product.run_lite_scan")
    def test_scan_accepts_supported_multilingual_examples(self, mocked_scan):
        mocked_scan.return_value = {"run_id": "run-multi", "cards": []}
        with tempfile.TemporaryDirectory() as tmp:
            actions = RadarProductActions(
                database=Path(tmp) / "radar.db",
                handoff_root=Path(tmp) / "handoffs",
            )
            expected = {
                "zh": "CN",
                "en": "US",
                "ja": "JP",
                "ko": "KR",
                "fr": "FR",
                "de": "DE",
                "es": "ES",
                "pt": "BR",
            }
            for language, country in expected.items():
                result = actions.run_scan({"scope": "AI Agents", "language": language})
                self.assertEqual(result["language"], language)
                self.assertEqual(result["country"], country)

    def test_invalid_language_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions = RadarProductActions(
                database=Path(tmp) / "radar.db",
                handoff_root=Path(tmp) / "handoffs",
            )
            with self.assertRaises(ValueError):
                actions.run_scan({"scope": "AI Agents", "language": "not a language"})

    @patch("radar_product.handoff_by_rank")
    def test_handoff_path_is_server_generated_under_fixed_root(self, mocked_handoff):
        mocked_handoff.return_value = {
            "source": {
                "topic": "Agent Skills / Safe Topic",
                "rank": 1,
                "radar_run_id": "run safe value",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db):
                pass
            root = Path(tmp) / "handoffs"
            actions = RadarProductActions(database=db, handoff_root=root)
            result = actions.create_handoff(
                {
                    "scope": "AI Agents",
                    "rank": 1,
                    "output_path": "ignored-client-value",
                }
            )
            path = Path(result["handoff_path"]).resolve()
            path.relative_to(root.resolve())
            self.assertTrue(path.is_file())
            self.assertNotIn("ignored-client-value", str(path))

    def test_handoff_rank_validation_rejects_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions = RadarProductActions(
                database=Path(tmp) / "radar.db",
                handoff_root=Path(tmp) / "handoffs",
            )
            with self.assertRaises(ValueError):
                actions.create_handoff({"scope": "AI Agents", "rank": 999})


if __name__ == "__main__":
    unittest.main()
