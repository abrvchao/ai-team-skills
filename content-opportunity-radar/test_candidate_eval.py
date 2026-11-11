from __future__ import annotations

import unittest

from candidate_eval import SCHEMA_VERSION, evidence_providers, score_labels


class CandidateEvalTests(unittest.TestCase):
    def test_evidence_providers_are_distinct_from_seed_providers(self):
        row = {
            "discovery": {"providers": ["github"]},
            "source_summary": [
                {"provider": "github"},
                {"provider": "google_news"},
                {"provider": "github"},
            ],
        }
        self.assertEqual(evidence_providers(row), ["github", "google_news"])

    def test_complete_top_k_reports_precision_and_false_positives(self):
        document = {
            "schema_version": SCHEMA_VERSION,
            "scope": "AI Agents",
            "run_id": "run-1",
            "items": [
                {"rank": 1, "topic_id": "a", "topic": "agent memory", "relevant": True},
                {
                    "rank": 2,
                    "topic_id": "b",
                    "topic": "python",
                    "relevant": False,
                    "note": "ambiguous snake/programming evidence",
                },
                {"rank": 3, "topic_id": "c", "topic": "agent evals", "relevant": True},
            ],
        }
        result = score_labels(document, k=3)
        self.assertEqual(result["label_coverage_at_k"], 1.0)
        self.assertEqual(result["precision_at_k"], 0.6667)
        self.assertEqual(result["false_positives"][0]["topic"], "python")

    def test_partial_labels_do_not_pretend_full_precision_at_k(self):
        document = {
            "schema_version": SCHEMA_VERSION,
            "scope": "AI Agents",
            "run_id": "run-1",
            "items": [
                {"rank": 1, "topic_id": "a", "topic": "agent memory", "relevant": True},
                {"rank": 2, "topic_id": "b", "topic": "agent evals", "relevant": None},
            ],
        }
        result = score_labels(document, k=2)
        self.assertEqual(result["label_coverage_at_k"], 0.5)
        self.assertEqual(result["labeled_precision_at_k"], 1.0)
        self.assertIsNone(result["precision_at_k"])

    def test_wrong_schema_fails_closed(self):
        with self.assertRaises(ValueError):
            score_labels({"schema_version": "wrong", "items": [{}]})


if __name__ == "__main__":
    unittest.main()
