from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core import AcquisitionMethod, Provenance, RawEvent
from entities import normalize_text
from discovery import (
    canonical_tokens,
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
    def test_text_and_token_normalization_use_bounded_cache_without_changing_output(self):
        normalize_text.cache_clear()
        canonical_tokens.cache_clear()
        text = "AI Agents &nbsp;&nbsp; Safety Platforms"
        first_norm = normalize_text(text)
        second_norm = normalize_text(text)
        first_tokens = canonical_tokens(text)
        second_tokens = canonical_tokens(text)
        self.assertEqual(first_norm, second_norm)
        self.assertEqual(first_tokens, second_tokens)
        self.assertGreaterEqual(normalize_text.cache_info().hits, 1)
        self.assertGreaterEqual(canonical_tokens.cache_info().hits, 1)
        self.assertEqual(normalize_text.cache_info().maxsize, 16384)
        self.assertEqual(canonical_tokens.cache_info().maxsize, 32768)

    def test_html_nbsp_entity_is_decoded_before_candidate_tokenization(self):
        self.assertEqual(
            normalize_text("AI Agents &nbsp;&nbsp; Example News"),
            "ai agents example news",
        )
        rows = [
            event(
                f"news-{i}",
                "google_news",
                title="AI safety platform for production agents",
                text="AI safety platform for production agents &nbsp;&nbsp; Example News",
            )
            for i in range(3)
        ]
        names = {row.name for row in discover_candidates(rows, scope="AI Safety", max_candidates=20)}
        self.assertNotIn("nbsp nbsp", names)

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
        self.assertNotIn("ai agent", explicit)

        candidates = discover_candidates(rows, scope="AI", max_candidates=10)
        names = {row.name for row in candidates}
        self.assertIn("agent memory architecture", names)
        self.assertIn("context engineering", names)
        self.assertNotIn("ai agent", names)

    def test_compound_ai_scope_requires_specific_token_not_generic_ai_only(self):
        generic = event(
            "generic-ai",
            "google_news",
            title="Generative AI models reshape software teams",
        )
        safety = event(
            "ai-safety",
            "google_news",
            title="AI safety evaluation framework for production systems",
        )

        # 中文说明：单独 AI 仍接收泛 AI 事件；AI Safety 必须额外命中 safety。
        self.assertTrue(extract_candidate_evidence([generic], scope="AI"))
        self.assertEqual(extract_candidate_evidence([generic], scope="AI Safety"), [])
        self.assertTrue(extract_candidate_evidence([safety], scope="AI Safety"))

    def test_agentic_alias_applies_to_agent_scope_but_not_other_ai_subscopes(self):
        row = event(
            "agentic",
            "google_news",
            title="Agentic orchestration patterns for production systems",
        )
        self.assertTrue(extract_candidate_evidence([row], scope="AI"))
        self.assertTrue(extract_candidate_evidence([row], scope="AI Agents"))
        self.assertEqual(extract_candidate_evidence([row], scope="AI Safety"), [])
        self.assertEqual(extract_candidate_evidence([row], scope="AI Coding"), [])

    def test_candidate_scope_gate_blocks_off_scope_topics_after_parent_event_passes(self):
        row = event(
            "mixed-coding",
            "github",
            title="AI coding tools for software teams",
            raw={
                "kind": "repository",
                "topics": ["claude-code", "job-search", "coding-agent", "video-production"],
            },
        )
        explicit = {item.phrase for item in extract_candidate_evidence([row], scope="AI Coding") if item.explicit}
        # 中文说明：父事件命中 coding 只负责入场；每个候选还要自己证明属于 AI Coding。
        self.assertIn("claude code", explicit)
        self.assertIn("coding agent", explicit)
        self.assertNotIn("job search", explicit)
        self.assertNotIn("video production", explicit)

    def test_candidate_scope_gate_keeps_valid_coding_concepts_without_substring_over_match(self):
        # "video" 含 "ide" 子串、"production" 以 "pr" 开头、"evaluation" 含 "eval"：
        # 精确 token 交集确保短别名不会因字符串包含而误放行离题候选。
        row = event(
            "boundary-coding",
            "github",
            title="AI coding tools for software teams",
            raw={
                "kind": "repository",
                "topics": [
                    "ai-ide",
                    "code-assistant",
                    "developer-tools",
                    "agent-skills",
                    "video-production",
                ],
            },
        )
        explicit = {
            item.phrase
            for item in extract_candidate_evidence([row], scope="AI Coding")
            if item.explicit
        }
        for valid in ("ai ide", "code assistant", "developer tools"):
            self.assertIn(valid, explicit)
        # 父事件已通过 AI Coding 门禁，但 "agent skills" 属于 AI Agents 语义族，候选级仍被拒绝。
        self.assertNotIn("agent skills", explicit)
        self.assertNotIn("video production", explicit)
        # 同一 "agent skills" 候选在自身范围下必须存活，证明是语义族判定而非产品名白名单。
        agent_row = event(
            "boundary-agents",
            "github",
            title="agent frameworks for production teams",
            raw={"kind": "repository", "topics": ["agent-skills"]},
        )
        agent_explicit = {
            item.phrase
            for item in extract_candidate_evidence([agent_row], scope="AI Agents")
            if item.explicit
        }
        self.assertIn("agent skills", agent_explicit)

    def test_candidate_scope_gate_covers_multi_token_gsc_queries_but_keeps_single_token_escape(self):
        # 中文说明：多 token 的第一方 GSC 查询也必须通过候选级范围门禁，避免
        # "job search" 绕过闸门；单 token 查询保留既有站内需求逃生通道。
        off_scope = event(
            "gsc-off",
            "gsc",
            raw={"dimensions": {"query": "job search", "page": "https://example.com/jobs"}},
        )
        in_scope = event(
            "gsc-on",
            "gsc",
            raw={"dimensions": {"query": "claude code review", "page": "https://example.com/cc"}},
        )
        single_token = event(
            "gsc-single",
            "gsc",
            raw={"dimensions": {"query": "python", "page": "https://example.com/py"}},
        )
        coding = {
            item.phrase
            for item in extract_candidate_evidence([off_scope, in_scope], scope="AI Coding")
            if item.explicit
        }
        self.assertNotIn("job search", coding)
        self.assertIn("claude code review", coding)
        agents = {
            item.phrase
            for item in extract_candidate_evidence([single_token], scope="AI Agents")
            if item.explicit
        }
        self.assertIn("python", agents)

    def test_ai_search_gate_rejects_job_search_ambiguity_but_keeps_retrieval_topics(self):
        row = event(
            "mixed-search",
            "github",
            title="AI search and retrieval infrastructure",
            raw={
                "kind": "repository",
                "topics": ["ai-job-search", "job-search", "semantic-retrieval", "vector-search"],
            },
        )
        explicit = {item.phrase for item in extract_candidate_evidence([row], scope="AI Search") if item.explicit}
        self.assertNotIn("ai job search", explicit)
        self.assertNotIn("job search", explicit)
        self.assertIn("semantic retrieval", explicit)
        self.assertIn("vector search", explicit)

    def test_public_single_token_topics_do_not_become_ambiguous_candidates(self):
        rows = [
            event(
                "gh1",
                "github",
                title="AI agent runtime implemented in Python",
                text="Open source runtime for production AI agents",
                raw={
                    "kind": "repository",
                    "topics": ["python", "open-source", "agent-memory"],
                },
            ),
            event(
                "news1",
                "google_news",
                title="14-foot python found near city park",
                text="Wildlife officials removed an invasive snake.",
            ),
        ]
        candidates = discover_candidates(rows, scope="AI Agents", max_candidates=20)
        names = {row.name for row in candidates}
        self.assertNotIn("python", names)
        self.assertNotIn("open source", names)
        self.assertIn("agent memory", names)

    def test_first_party_gsc_can_seed_single_token_query(self):
        rows = [
            event(
                "gsc-python",
                "gsc",
                raw={
                    "dimensions": {
                        "query": "python",
                        "page": "https://example.com/python",
                    }
                },
            )
        ]
        candidates = discover_candidates(rows, scope="AI Agents", max_candidates=10)
        self.assertEqual([row.name for row in candidates], ["python"])

    def test_title_phrases_require_scope_support(self):
        rows = [
            event(
                "relevant",
                "google_news",
                title="Agent memory architecture gains adoption",
            ),
            event(
                "noise",
                "google_news",
                title="Python snake rescue draws crowds",
                text="Wildlife story with no artificial intelligence context.",
            ),
        ]
        evidence = extract_candidate_evidence(rows, scope="AI Agents")
        event_ids = {item.event_id for item in evidence}
        self.assertIn("relevant", event_ids)
        self.assertNotIn("noise", event_ids)

    def test_agentic_evidence_supports_ai_scopes_without_global_permissiveness(self):
        """Observed "agentic" context must satisfy AI/agent scopes only.

        This fails if scope filtering is disabled (the unrelated Chinese event
        would leak through) or if the scope map is made globally permissive
        (the agentic event would satisfy the unrelated "Finance" scope).
        """
        agentic = event(
            "agentic",
            "google_news",
            title="Agentic coding platform ships autonomous workflow automation",
            text="A deterministic runtime for agentic coding tasks.",
        )
        unrelated = event(
            "unrelated",
            "google_news",
            title="城市公园发现一条十四英尺长的蟒蛇",
            text="野生动物官员移除了这条入侵蛇类。",
        )
        for scope in ("AI", "AI Agents"):
            with self.subTest(scope=scope):
                supported = {
                    item.event_id
                    for item in extract_candidate_evidence([agentic], scope=scope)
                }
                self.assertIn("agentic", supported)
                blocked = {
                    item.event_id
                    for item in extract_candidate_evidence([unrelated], scope=scope)
                }
                self.assertNotIn("unrelated", blocked)
        self.assertEqual(extract_candidate_evidence([agentic], scope="Finance"), [])
        self.assertEqual(extract_candidate_evidence([unrelated], scope="Finance"), [])

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
