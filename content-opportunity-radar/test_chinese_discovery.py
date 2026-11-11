"""Offline Chinese Candidate Discovery V1 regression (real AIHOT observations)."""
import json
from pathlib import Path
import unittest

from core import RawEvent
from discovery import discover_candidates, extract_candidate_evidence
from entities import normalize_text
from test_discovery import event


class ChineseDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (Path(__file__).parent / "fixtures/aihot_chinese_v1.json").read_text()
        )
        cls.events = [RawEvent.from_dict(e) for e in cls.fixture["events"]]
        cls.recovered = [
            RawEvent.from_dict(e) for e in cls.fixture["recovered_original_issue_events"]
        ]

    def test_original_issue_ids_retain_meaningful_topics(self):
        candidates = discover_candidates(self.recovered, scope="AI Agents")
        names = {c.name for c in candidates}
        self.assertEqual(names, {
            "开源智能体安全平台", "AI Agent 审计 13 个基准", "AI 智能体安全事件",
        })
        self.assertEqual(
            {i for c in candidates for i in c.evidence_ids},
            {e.id for e in self.recovered},
        )
        phrases = {
            normalize_text(p.phrase)
            for p in extract_candidate_evidence(self.recovered, scope="AI Agents")
        }
        self.assertTrue(phrases.isdisjoint({"nvidia 推出", "13 个基准"}))

    def test_real_agentic_event_supports_ai_and_agent_scopes(self):
        # Real AIHOT event a59d9623107f219540f6c0e1: observed "agentic 编码"
        # evidence must keep GPT-6.1 Sol discoverable under both scopes, and the
        # unscoped result must stay unchanged for parity.
        target = next(e for e in self.events if e.id == "a59d9623107f219540f6c0e1")
        for scope in (None, "AI", "AI Agents"):
            with self.subTest(scope=scope):
                candidates = discover_candidates([target], scope=scope)
                self.assertIn("GPT-6.1 Sol", {c.name for c in candidates})
                self.assertIn(
                    target.id,
                    {i for c in candidates for i in c.evidence_ids},
                )

    def test_real_agentic_event_stays_blocked_for_unrelated_scope(self):
        target = next(e for e in self.events if e.id == "a59d9623107f219540f6c0e1")
        self.assertEqual(discover_candidates([target], scope="Marketing"), [])
        self.assertEqual(extract_candidate_evidence([target], scope="Marketing"), [])
        # 中文说明：包含 AI 泛词的复合范围也不能绕过具体词门槛。
        self.assertEqual(discover_candidates([target], scope="AI Safety"), [])
        self.assertEqual(extract_candidate_evidence([target], scope="AI Safety"), [])

    def test_real_ten_keep_useful_candidates_and_versions(self):
        self.assertEqual(len(self.events), 10)
        names = {c.name for c in discover_candidates(self.events, scope="AI Agents")}
        self.assertTrue({
            "GPT-6.1 Sol", "AI Agent 做回归测试", "Agents API",
            "构建 AI 智能体", "测试 AI Agent 的工具调用准确性",
        } <= names)
        self.assertFalse({"ai 发布", "openrouter 教程"} & names)
        self.assertGreaterEqual(len(names), 5)  # Rejecting everything is not a fix.

    def test_every_candidate_phrase_is_an_observed_span(self):
        events = self.events + self.recovered
        by_id = {e.id: e for e in events}
        for item in extract_candidate_evidence(events, scope="AI Agents"):
            source = by_id[item.event_id]
            haystack = normalize_text((source.title or "") + " " + (source.text or ""))
            self.assertIn(normalize_text(item.phrase), haystack)

    def test_replay_is_deterministic(self):
        def run(events):
            return [c.to_dict() for c in discover_candidates(events, scope="AI Agents")]
        self.assertEqual(run(self.events), run(list(reversed(self.events))))

    def test_action_and_quantity_fragments_are_not_topics(self):
        for title in ["NVIDIA 推出", "13 个基准", "42 项更新", "AI 发布", "AI 智能体"]:
            with self.subTest(title=title):
                self.assertEqual(extract_candidate_evidence([
                    event("fragment", "aihot", title=title)
                ]), [])

    def test_action_verb_compound_topics_remain_discoverable(self):
        # 验收正例：动作动词字符出现在复合名词内部时，不得被当作无条件内部边界删掉。
        for title in ["安全审计支持平台", "智能支持系统", "开源包含套件", "多模态支持平台"]:
            with self.subTest(title=title):
                candidates = discover_candidates([event("compound", "aihot", title=title)])
                self.assertIn(title, {c.name for c in candidates})
                rows = extract_candidate_evidence([
                    event("compound", "aihot", title=title)
                ])
                self.assertEqual([r.phrase for r in rows], [title])

    def test_internal_action_verb_with_four_char_prefix_keeps_full_compound(self):
        # 缺陷复现：左侧 4 字合法片段 智能客服 曾在内部动作动词 支持 处短路整段回退，
        # 把 智能客服支持平台 截成过于泛化的 智能客服。覆盖性门禁必须保留完整观测复合词。
        title = "智能客服支持平台"
        rows = extract_candidate_evidence([event("compound4", "aihot", title=title)])
        self.assertEqual([r.phrase for r in rows], [title])
        candidates = discover_candidates([event("compound4", "aihot", title=title)])
        self.assertIn(title, {c.name for c in candidates})

    def test_action_verb_boundary_preserves_full_compound_by_left_fragment_length(self):
        # 边界参数化：左片段长度 2/3/4/5/6 分别配合 支持/包含/提供。长度 <4 由原有
        # 整段回退保底，长度 ≥4 由覆盖性门禁保底；任何长度都不得截成左侧局部片段。
        cases = [
            ("支持", "平台", ["智能", "多模态", "智能客服", "企业级客服", "智能客服系统"]),
            ("包含", "套件", ["开源", "数据流", "内容审核", "分布式存储", "多模态数据集"]),
            ("提供", "能力", ["云端", "大模型", "智能客服", "企业级客服", "智能客服系统"]),
        ]
        for verb, suffix, prefixes in cases:
            for expected_len, prefix in zip(range(2, 7), prefixes):
                title = f"{prefix}{verb}{suffix}"
                with self.subTest(verb=verb, left_len=expected_len, title=title):
                    self.assertEqual(len(prefix), expected_len)
                    rows = extract_candidate_evidence([
                        event("boundary", "aihot", title=title)
                    ])
                    self.assertEqual([r.phrase for r in rows], [title])
                    candidates = discover_candidates([
                        event("boundary", "aihot", title=title)
                    ])
                    self.assertIn(title, {c.name for c in candidates})

    def test_known_fragments_stay_suppressed_after_coverage_fix(self):
        # 覆盖性门禁不得放宽既有抑制：动作/数量/体助词/跨语言谓语碎片仍须为空。
        for title in [
            "NVIDIA 推出", "AI 发布", "13 个基准", "42 项更新",
            "推出新平台", "发布新模型", "推出了新平台",
            "研究过支持系统", "讨论着支持系统",
            "NVIDIA 推出了新平台", "OpenAI 发布了 GPT-6",
        ]:
            with self.subTest(title=title):
                self.assertEqual(extract_candidate_evidence([
                    event("fragment", "aihot", title=title)
                ]), [])

    def test_coverage_gate_keeps_clause_boundaries(self):
        # 覆盖性门禁只在标点硬边界内的单个 segment 生效，不得跨子句拼接。
        rows = extract_candidate_evidence([
            event("coverage-clauses", "aihot", title="智能客服支持平台，提供持续监控")
        ])
        self.assertEqual({r.phrase for r in rows}, {"智能客服支持平台", "持续监控"})

    def test_known_bad_and_quantity_fragments_stay_suppressed(self):
        # 验收反例：已知坏标题碎片、纯数量/动作碎片都不得成为候选。
        for title in [
            "NVIDIA 推出", "AI 发布", "13 个基准",
            "42 项更新", "推出新平台", "发布新模型",
        ]:
            with self.subTest(title=title):
                self.assertEqual(extract_candidate_evidence([
                    event("fragment", "aihot", title=title)
                ]), [])

    def test_leading_action_verb_keeps_the_real_noun_compound(self):
        # 剥离前导动词后仍有独立合法名词时保留名词，而不是把真实复合词一起删掉。
        rows = extract_candidate_evidence([
            event("lead", "aihot", title="推出多模态支持平台")
        ])
        self.assertEqual([r.phrase for r in rows], ["多模态支持平台"])

    def test_aspect_particle_leakage_is_not_a_phrase(self):
        rows = extract_candidate_evidence([
            event("aspect", "aihot", title="NVIDIA 推出了新平台"),
            event("aspect2", "aihot", title="OpenAI 发布了 GPT-6"),
        ])
        for row in rows:
            with self.subTest(phrase=row.phrase):
                self.assertFalse(normalize_text(row.phrase).startswith(("了", "着", "过")))

    def test_lexical_morpheme_compounds_are_discoverable(self):
        # 回归：过/着 作词首构词语素（过拟合、着色）的纯汉字复合词曾被整段回退
        # 的体助词否决误杀；短语与候选都必须保留，且仍是观测到的原文 span。
        for title in ["过拟合支持系统", "过拟合支持平台", "着色支持工具"]:
            with self.subTest(title=title):
                rows = extract_candidate_evidence([
                    event("lexical", "aihot", title=title)
                ])
                self.assertEqual([r.phrase for r in rows], [title])
                candidates = discover_candidates([
                    event("lexical", "aihot", title=title)
                ])
                self.assertIn(title, {c.name for c in candidates})

    def test_true_aspect_and_predicate_leakage_stays_suppressed(self):
        # 反例：了/着/过 作动词后缀或谓语时仍须拦截，既覆盖纯汉字标题
        # （推出了新平台、研究过支持系统、讨论着支持系统），也覆盖跨语言标题。
        for title in [
            "推出了新平台", "研究过支持系统", "讨论着支持系统",
            "NVIDIA 推出了新平台", "OpenAI 发布了 GPT-6",
        ]:
            with self.subTest(title=title):
                self.assertEqual(extract_candidate_evidence([
                    event("leak", "aihot", title=title)
                ]), [])

    def test_lexical_morpheme_rule_generalizes_without_whitelist(self):
        # 结构规则按词法位置判定（词首 过/着 为构词语素，其余位置为体助词），
        # 应对未列入缺陷清单的新词同样生效，证明修复不是针对样例的白名单。
        for title in ["过滤支持系统", "着陆支持平台"]:
            with self.subTest(title=title):
                rows = extract_candidate_evidence([
                    event("generalize", "aihot", title=title)
                ])
                self.assertEqual([r.phrase for r in rows], [title])
        for title in ["使用过支持系统", "讨论着支持系统"]:
            with self.subTest(title=title):
                self.assertEqual(extract_candidate_evidence([
                    event("generalize", "aihot", title=title)
                ]), [])

    def test_clauses_do_not_get_stitched_together(self):
        rows = extract_candidate_evidence([
            event("clauses", "aihot", title="NVIDIA 推出 Open Agent Safety Platform，提供芯片级持续监控")
        ])
        self.assertEqual({r.phrase for r in rows}, {
            "Open Agent Safety Platform", "芯片级持续监控",
        })

    def test_chinese_scope_and_empty_title_summary_fallback(self):
        rows = extract_candidate_evidence([
            event("zh", "aihot", text="厂商发布智能体安全平台，提供持续监控")
        ], scope="智能体")
        self.assertIn("智能体安全平台", {r.phrase for r in rows})

    def test_first_party_chinese_query_stays_explicit(self):
        rows = extract_candidate_evidence([
            event("gsc", "gsc", raw={"dimensions": {"query": "13 个基准"}})
        ], scope="AI")
        self.assertEqual([r.phrase for r in rows], ["13 个基准"])
        self.assertTrue(rows[0].explicit)


if __name__ == "__main__":
    unittest.main()
