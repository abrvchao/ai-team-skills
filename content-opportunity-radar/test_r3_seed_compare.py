from __future__ import annotations

import unittest

from r3_seed_compare import score_compare


def doc(scope, prefix, labels):
    return {
        "schema_version": "candidate-eval-v1",
        "scope": scope,
        "run_id": prefix,
        "items": [
            {"rank": i, "topic_id": f"{prefix}-{i}", "topic": f"topic {i}", "relevant": label, "note": ""}
            for i, label in enumerate(labels, 1)
        ],
    }


class R3SeedCompareTests(unittest.TestCase):
    def test_comparison_waits_for_both_human_label_sets(self):
        native = {"queues": [{"document": doc("AI Agents", "n", [True, True, True, False, False])}]}
        experimental = doc("AI Agents", "e", [None, None, None, None, None])
        result = score_compare(native_batch=native, experimental=experimental)
        self.assertFalse(result["complete"])
        self.assertEqual(result["comparison"], "pending_human_labels")
        self.assertFalse(result["automatic_provider_promotion"])

    def test_comparison_reports_precision_delta_without_promoting_provider(self):
        native = {"queues": [{"document": doc("AI Agents", "n", [True, True, True, False, False])}]}
        experimental = doc("AI Agents", "e", [True, True, True, True, False])
        result = score_compare(native_batch=native, experimental=experimental)
        self.assertTrue(result["complete"])
        self.assertEqual(result["native"]["precision_at_k"], 0.6)
        self.assertEqual(result["experimental"]["precision_at_k"], 0.8)
        self.assertEqual(result["precision_delta_experimental_minus_native"], 0.2)
        self.assertEqual(result["comparison"], "experimental_higher_precision")
        self.assertFalse(result["automatic_provider_promotion"])


if __name__ == "__main__":
    unittest.main()
