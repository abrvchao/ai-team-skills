from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from r3_label_pack import _select_validation_documents


def candidate(name: str):
    slug = name.lower().replace(" ", "-")
    return SimpleNamespace(
        id=f"topic:{slug}",
        name=name,
        discovery_score=50.0,
        providers=["github"],
        aliases=[name],
        evidence_ids=[f"event:{slug}"],
    )


class R3LabelPackTests(unittest.TestCase):
    def test_selection_skips_underfilled_scope_and_stops_after_twenty_unique_topics(self):
        rows = {
            "sparse": [candidate(f"sparse {i}") for i in range(4)],
            "a": [candidate(f"a {i}") for i in range(5)],
            "b": [candidate(f"b {i}") for i in range(5)],
            "c": [candidate(f"c {i}") for i in range(5)],
            "d": [candidate(f"d {i}") for i in range(5)],
            "unused": [candidate(f"unused {i}") for i in range(5)],
        }
        with patch("r3_label_pack.discover_candidates", side_effect=lambda events, scope, max_candidates: rows[scope]):
            docs, selection = _select_validation_documents(
                [], scopes=list(rows), k=5, minimum_unique_topics=20
            )
        self.assertEqual([doc["scope"] for doc in docs], ["a", "b", "c", "d"])
        self.assertEqual(selection["selected_unique_topics"], 20)
        self.assertEqual(selection["skipped_scopes"][0]["scope"], "sparse")
        self.assertFalse(selection["label_dependent"])
        self.assertTrue(all(item["relevant"] is None for doc in docs for item in doc["items"]))

    def test_duplicate_topics_require_more_scopes_before_selection_is_ready(self):
        shared = [candidate(f"shared {i}") for i in range(5)]
        rows = {
            "a": shared,
            "b": shared,
            "c": [candidate(f"c {i}") for i in range(5)],
            "d": [candidate(f"d {i}") for i in range(5)],
            "e": [candidate(f"e {i}") for i in range(5)],
        }
        with patch("r3_label_pack.discover_candidates", side_effect=lambda events, scope, max_candidates: rows[scope]):
            docs, selection = _select_validation_documents(
                [], scopes=list(rows), k=5, minimum_unique_topics=20
            )
        self.assertEqual([doc["scope"] for doc in docs], ["a", "b", "c", "d", "e"])
        self.assertEqual(selection["selected_unique_topics"], 20)

    def test_selection_fails_closed_when_real_evidence_cannot_reach_minimum(self):
        rows = {"a": [candidate(f"a {i}") for i in range(5)], "b": [candidate(f"b {i}") for i in range(5)]}
        with patch("r3_label_pack.discover_candidates", side_effect=lambda events, scope, max_candidates: rows[scope]):
            with self.assertRaisesRegex(ValueError, "evidence is insufficient"):
                _select_validation_documents([], scopes=list(rows), k=5, minimum_unique_topics=20)


if __name__ == "__main__":
    unittest.main()
