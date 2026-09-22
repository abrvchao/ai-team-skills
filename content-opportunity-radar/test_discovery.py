from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core import AcquisitionMethod, Provenance, RawEvent
from discovery import (
    CandidateEvidence,
    CandidateTopic,
    alias_similarity,
    assign_events_exclusively,
    cluster_candidates,
    discover_candidates,
    extract_candidate_evidence,
    overlap_penalized_rank,
)


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def event(
    event_id: str,
    provider: str,
    *,
    title: str = "",
    text: str = "",
    raw=None,
) -> RawEvent:
    return RawEvent(
        id=event_id,
        provider=provider,
        source=provider,
        acquisition_method=AcquisitionMethod.PUBLIC_WEB_API,
        retrieved_at=NOW,
        title=title,
        text=text,
        raw=raw,
        provenance=Provenance("test"),
    )


class DiscoveryTests(unittest.TestCase):
    def test_alias_normalization_merges_singular_plural(self):
        self.assertEqual(alias_similarity("AI agent memory", "AI agents memory"), 1.0)
        rows = cluster_candidates([
            CandidateEvidence("AI agent memory", "github", "g1", explicit=True, weight=2),
            CandidateEvidence("AI agents memory", "hackernews", "h1", weight=1),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider_count, 2)
        self.assertEqual(set(rows[0].evidence_ids), {"g1", "h1"})

    def test_cross_source_candidate_beats_single_provider_flood(self):
        flood = [
            CandidateEvidence("agent memory", "github", f"g{i}")
            for i in range(20)
        ]
        diverse = [
            CandidateEvidence("context engineering", provider, f"d{i}")
            for i, provider in enumerate(
                ["github", "hackernews", "google_news", "gdelt"]
            )
        ]
        rows = cluster_candidates([*flood, *diverse])
        scores = {row.name: row.discovery_score for row in rows}
        self.assertGreater(
            scores["context engineering"],
            scores["agent memory"],
        )

    def test_each_event_is_assigned_to_at_most_one_candidate(self):
        candidates = [
            CandidateTopic(
                id="topic:agent-memory",
                name="agent memory",
                aliases=["agent memory"],
                discovery_score=80,
                provider_count=2,
                event_count=2,
                evidence_ids=[],
                providers=["github", "hackernews"],
            ),
            CandidateTopic(
                id="topic:agent-memory-architecture",
                name="agent memory architecture",
                aliases=["agent memory architecture"],
                discovery_score=70,
                provider_count=2,
                event_count=2,
                evidence_ids=[],
                providers=["github", "google_news"],
            ),
        ]
        events = [
            event(
                "e1",
                "github",
                title="Agent memory architecture patterns for production systems",
            )
        ]
        assigned = assign_events_exclusively(events, candidates)
        occurrences = sum(
            1
            for ids in assigned.values()
            if "e1" in ids
        )
        self.assertEqual(occurrences, 1)
        self.assertIn("e1", assigned["topic:agent-memory"])

    def test_explicit_gsc_queries_and_github_topics_seed_candidates(self):
        rows = [
            event(
                "gsc1",
                "gsc",
                raw={
                    "dimensions": {
                        "query": "agent memory architecture",
                        "page": "https://example.com/a",
                    }
                },
            ),
            event(
                "gh1",
                "github",
                title="owner/repo",
                raw={
                    "kind": "repository",
                    "topics": ["context-engineering", "ai-agent"],
                },
            ),
        ]
        evidence = extract_candidate_evidence(rows, scope="AI")
        explicit = {item.phrase for item in evidence if item.explicit}
        self.assertIn("agent memory architecture", explicit)
        self.assertIn("context engineering", explicit)
        self.assertIn("ai agent", explicit)

        candidates = discover_candidates(rows, scope="AI", max_candidates=10)
        names = {row.name for row in candidates}
        self.assertIn("agent memory architecture", names)
        self.assertIn("context engineering", names)

    def test_discovery_does_not_need_llm_or_synthetic_topic(self):
        rows = [
            event(
                "n1",
                "google_news",
                title="Developers debate context engineering for AI systems",
            ),
            event(
                "h1",
                "hackernews",
                title="Context engineering patterns for production agents",
            ),
        ]
        candidates = discover_candidates(rows, scope="AI", max_candidates=10)
        self.assertTrue(candidates)
        observed = " ".join(
            [rows[0].title, rows[1].title]
        ).casefold()
        for candidate in candidates:
            for token in candidate.name.split():
                self.assertIn(token.casefold(), observed)

    def test_overlap_penalty_keeps_duplicate_evidence_from_filling_top_slots(self):
        reports = [
            {
                "topic": "A",
                "opportunity": {
                    "score": 90,
                    "evidence_ids": ["e1", "e2"],
                },
            },
            {
                "topic": "B",
                "opportunity": {
                    "score": 85,
                    "evidence_ids": ["e1", "e2"],
                },
            },
            {
                "topic": "C",
                "opportunity": {
                    "score": 80,
                    "evidence_ids": ["e3"],
                },
            },
        ]
        ranked = overlap_penalized_rank(reports)
        self.assertEqual([row["topic"] for row in ranked], ["A", "C", "B"])
        self.assertEqual(ranked[0]["evidence_overlap_ratio"], 0.0)
        duplicate = next(row for row in ranked if row["topic"] == "B")
        self.assertEqual(duplicate["evidence_overlap_ratio"], 1.0)
        self.assertLess(duplicate["rank_score"], 80)

    def test_tie_order_is_stable(self):
        reports = [
            {"topic": "Beta", "opportunity": {"score": 70, "evidence_ids": ["b"]}},
            {"topic": "Alpha", "opportunity": {"score": 70, "evidence_ids": ["a"]}},
        ]
        first = overlap_penalized_rank(reports)
        second = overlap_penalized_rank(list(reversed(reports)))
        self.assertEqual(
            [row["topic"] for row in first],
            [row["topic"] for row in second],
        )


if __name__ == "__main__":
    unittest.main()
