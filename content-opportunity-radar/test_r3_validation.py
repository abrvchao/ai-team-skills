from __future__ import annotations

import unittest

from candidate_eval import SCHEMA_VERSION
from r3_validation import BATCH_SCHEMA_VERSION, build_batch, render_markdown, score_batch


def queue(scope: str, *, relevant: int = 3, labeled_items: int = 5):
    items = []
    for rank in range(1, 6):
        label = None
        if rank <= labeled_items:
            label = rank <= relevant
        items.append({
            "rank": rank,
            "topic_id": f"{scope}-{rank}",
            "topic": f"topic {rank}",
            "relevant": label,
            "note": "bad" if label is False else "",
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "run_id": f"run-{scope}",
        "items": items,
    }


class R3ValidationTests(unittest.TestCase):
    def test_four_complete_top5_queues_pass_twenty_label_gate_at_point_six(self):
        batch = build_batch([queue(f"scope-{i}") for i in range(4)])
        result = score_batch(batch)
        self.assertEqual(result["total_labeled_opportunities"], 20)
        self.assertEqual(result["unique_labeled_topics"], 20)
        self.assertTrue(result["unique_topic_gate_pass"])
        self.assertEqual(result["micro_precision_at_k"], 0.6)
        self.assertEqual(result["macro_precision_at_k"], 0.6)
        self.assertTrue(result["passed"])

    def test_incomplete_top5_never_pretends_gate_passed(self):
        docs = [queue("a", labeled_items=4), queue("b"), queue("c"), queue("d")]
        batch = build_batch(docs)
        result = score_batch(batch)
        self.assertFalse(result["all_top_k_complete"])
        self.assertFalse(result["passed"])

    def test_enough_precision_without_twenty_labels_does_not_pass(self):
        batch = build_batch([queue("a", relevant=5), queue("b", relevant=5)])
        result = score_batch(batch)
        self.assertEqual(result["total_labeled_opportunities"], 10)
        self.assertEqual(result["micro_precision_at_k"], 1.0)
        self.assertFalse(result["label_count_gate_pass"])
        self.assertFalse(result["passed"])

    def test_markdown_keeps_human_label_truth_rule_visible(self):
        result = score_batch(build_batch([queue(f"s{i}") for i in range(4)]))
        report = render_markdown(result)
        self.assertIn("人工已标注机会", report)
        self.assertIn("模型自评", report)
        self.assertIn("PASS（通过）", report)
        self.assertIn("分级相关性", report)

    def test_graded_relevance_tracks_strength_without_changing_frozen_binary_gate(self):
        doc = queue("graded", relevant=5)
        levels = ["strong", "medium", "weak", "unrelated", "uncertain"]
        binary = [True, True, None, False, None]
        for item, level, relevant in zip(doc["items"], levels, binary):
            item["relevance_level"] = level
            item["relevant"] = relevant
        result = score_batch(build_batch([doc]))
        row = result["queues"][0]
        self.assertEqual(row["graded_labeled_at_k"], 4)
        self.assertEqual(row["graded_coverage_at_k"], 0.8)
        self.assertEqual(row["mean_relevance_strength_at_k"], 0.5)
        self.assertEqual(result["graded_mean_relevance_at_k"], 0.5)
        self.assertEqual(result["graded_relevance_coverage_at_k"], 0.8)
        self.assertFalse(result["passed"])


    def test_duplicate_topics_across_scopes_cannot_fake_twenty_unique_topics(self):
        docs = []
        for i in range(4):
            doc = queue(f"scope-{i}")
            for rank, item in enumerate(doc["items"], 1):
                item["topic_id"] = f"shared-{rank}"
                item["topic"] = f"shared topic {rank}"
            docs.append(doc)
        result = score_batch(build_batch(docs))
        self.assertEqual(result["total_labeled_opportunities"], 20)
        self.assertEqual(result["unique_labeled_topics"], 5)
        self.assertFalse(result["unique_topic_gate_pass"])
        self.assertFalse(result["passed"])

    def test_wrong_batch_schema_fails_closed(self):
        with self.assertRaises(ValueError):
            score_batch({"schema_version": "wrong", "queues": []})


if __name__ == "__main__":
    unittest.main()
