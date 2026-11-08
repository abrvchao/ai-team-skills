"""验证本地只读网页/API 不触发扫描且使用真实 V2 表。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from radar_v2_p0_replay import replay_history
from radar_v2_viewer import create_server, read_summary
from read_model import OpportunityReadStore


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        source_path = cls.base / "source.db"
        with OpportunityReadStore(source_path) as source:
            for idx, topics in enumerate(
                [[("a", 80), ("b", 70)], [("a", 70), ("c", 90)]]
            ):
                source.record_discovery({
                    "scope": "AI Agents",
                    "scanned_count": 2,
                    "candidate_count": 2,
                    "seed": {"source_mode": "test"},
                    "top_opportunities": [
                        {"topic_id": topic, "topic": topic,
                         "opportunity": {"score": score},
                         "discovery": {"discovery_score": score}}
                        for topic, score in topics
                    ],
                }, generated_at=datetime(
                    2026, 10, 9 + idx, 10, tzinfo=timezone.utc
                ))
        replay_history(source_path, cls.base / "output")
        cls.database = cls.base / "output/replay.db"
        cls.server = create_server(cls.database, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def test_api_really_reads_persisted_changes(self):
        with urllib.request.urlopen(self.url + "/api/v2/summary") as resp:
            payload = json.load(resp)
        self.assertEqual(payload["mode"], "offline_historical_replay_not_new_collection")
        self.assertEqual(payload["change_count"], 3)
        self.assertEqual(len(payload["runs"]), 2)
        self.assertEqual(payload["provider_api_calls_during_view"], 0)

    def test_page_has_multilingual_and_no_external_dependencies(self):
        with urllib.request.urlopen(self.url + "/") as resp:
            content = resp.read().decode("utf-8")
        for text in ("中文", "English", "日本語", "한국어", "/api/v2/summary"):
            self.assertIn(text, content)
        self.assertNotIn("<script src=", content)

    def test_health_is_read_only(self):
        with urllib.request.urlopen(self.url + "/health") as resp:
            self.assertEqual(json.load(resp), {"status": "ok", "read_only": True})

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.url + "/sensitive-file")
        self.assertEqual(context.exception.code, 404)

    def test_api_has_no_secret_fields(self):
        data = read_summary(self.database)
        for record in data["changes"]:
            self.assertNotIn("details_json", record)
            self.assertNotIn("evidence_ids_json", record)


if __name__ == "__main__":
    unittest.main()
