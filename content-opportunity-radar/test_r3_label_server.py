from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from r3_label_server import ACTION_HEADER, ACTION_VALUE, make_handler, set_judgment, set_label, set_relevance_level


def sample_batch():
    return {
        "schema_version":"r3-validation-batch-v1",
        "gate":{"minimum_labeled_opportunities":20,"minimum_unique_labeled_topics":20,"k":5,"minimum_precision_at_k":0.6},
        "queues":[{
            "scope":"AI Agents","workspace_id":None,"run_id":"r1","source_path":None,
            "document":{"schema_version":"candidate-eval-v1","scope":"AI Agents","run_id":"r1","items":[
                {"rank":i,"topic_id":f"t{i}","topic":f"topic {i}","relevant":None,"note":""}
                for i in range(1,6)
            ]},
        }],
    }


class R3LabelServerTests(unittest.TestCase):
    def test_set_label_changes_only_human_fields(self):
        batch = sample_batch()
        before_topic = batch["queues"][0]["document"]["items"][0]["topic"]
        item = set_label(batch, scope="AI Agents", run_id="r1", rank=1, relevant=False, note=" too broad ")
        self.assertFalse(item["relevant"])
        self.assertEqual(item["human_judgment"], "unreliable")
        self.assertEqual(item["note"], "too broad")
        self.assertEqual(item["topic"], before_topic)

    def test_uncertain_judgment_is_saved_but_not_counted_as_binary_label(self):
        batch = sample_batch()
        item = set_judgment(batch, scope="AI Agents", run_id="r1", rank=2, judgment="uncertain", note=" borderline ")
        self.assertIsNone(item["relevant"])
        self.assertEqual(item["human_judgment"], "uncertain")
        self.assertEqual(item["note"], "borderline")

    def test_weak_relevance_is_distinct_from_uncertainty_and_keeps_binary_gate_open(self):
        batch = sample_batch()
        item = set_relevance_level(batch, scope="AI Agents", run_id="r1", rank=3, relevance_level="weak", note="weak relation")
        self.assertEqual(item["relevance_level"], "weak")
        self.assertAlmostEqual(item["relevance_score"], 1 / 3)
        self.assertIsNone(item["relevant"])
        self.assertEqual(item["human_judgment"], "uncertain")

    def test_medium_relevance_counts_as_binary_relevant_without_losing_strength(self):
        batch = sample_batch()
        item = set_relevance_level(batch, scope="AI Agents", run_id="r1", rank=4, relevance_level="medium")
        self.assertEqual(item["relevance_level"], "medium")
        self.assertTrue(item["relevant"])
        self.assertAlmostEqual(item["relevance_score"], 2 / 3)

    def test_http_requires_human_action_header_then_persists_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"
            path.write_text(json.dumps(sample_batch()), encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1",0), make_handler(path))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port=server.server_address[1]
                body=json.dumps({"scope":"AI Agents","run_id":"r1","rank":1,"relevance_level":"strong","note":"useful"})
                conn=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
                conn.request("POST","/api/label",body=body,headers={"Content-Type":"application/json"})
                resp=conn.getresponse(); resp.read()
                self.assertEqual(resp.status,403)

                conn=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
                conn.request("POST","/api/label",body=body,headers={"Content-Type":"application/json",ACTION_HEADER:ACTION_VALUE})
                resp=conn.getresponse(); payload=json.loads(resp.read())
                self.assertEqual(resp.status,200)
                self.assertTrue(payload["saved"])
                saved=json.loads(path.read_text())
                self.assertTrue(saved["queues"][0]["document"]["items"][0]["relevant"])
                self.assertEqual(saved["queues"][0]["document"]["items"][0]["human_judgment"], "reliable")
                self.assertEqual(saved["queues"][0]["document"]["items"][0]["relevance_level"], "strong")
                self.assertTrue((Path(tmp)/"result.json").is_file())
                self.assertTrue((Path(tmp)/"R3_VALIDATION_REPORT.md").is_file())
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_invalid_non_boolean_label_fails_closed(self):
        batch=sample_batch()
        with self.assertRaises(ValueError):
            set_label(batch, scope="AI Agents", run_id="r1", rank=1, relevant="yes")


if __name__ == "__main__":
    unittest.main()
