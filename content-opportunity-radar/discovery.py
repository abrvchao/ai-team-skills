"""Candidate discovery and clustering for Opportunity Discovery Loop V1.

This module never invents topics with an LLM. Candidate topics are extracted
from observed source text, Search Console queries, and GitHub repository topics.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping, Sequence

from core import RawEvent, clamp
from entities import normalize_text, topic_id


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "for", "from", "has", "have", "how", "i", "in", "into", "is", "it", "its",
    "new", "news", "of", "on", "or", "our", "out", "over", "that", "the",
    "their", "this", "to", "up", "via", "was", "we", "what", "when", "where",
    "which", "who", "why", "with", "you", "your",
    "launch", "launches", "launched", "announces", "announced", "introduces",
    "today", "latest", "best", "top", "using", "use", "used", "guide",
}

GENERIC_SINGLE_TOKENS = {
    "ai", "llm", "ml", "tech", "technology", "software", "open", "source",
    "github", "agent", "agents", "model", "models", "data", "app", "apps",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.-]*|[\u4e00-\u9fff]{2,}")


def _singular(token: str) -> str:
    token = token.casefold()
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


@lru_cache(maxsize=32768)
def canonical_tokens(text: str) -> tuple[str, ...]:
    # 中文说明：候选聚类和事件归属会反复读取同一短语；有界缓存避免百万次重复分词。
    # 返回 tuple（元组）保持不可变，适合安全缓存；缓存满后自动淘汰旧项。
    tokens = []
    for raw in TOKEN_RE.findall(text):
        token = _singular(normalize_text(raw))
        if not token or token in STOPWORDS:
            continue
        tokens.append(token)
    return tuple(tokens)


def _clean_phrase(tokens: Sequence[str]) -> str:
    return " ".join(token for token in tokens if token).strip()


def _valid_phrase(
    phrase: str,
    *,
    allow_generic: bool = False,
) -> bool:
    tokens = canonical_tokens(phrase)
    if not tokens:
        return False
    if not allow_generic and all(token in GENERIC_SINGLE_TOKENS for token in tokens):
        return False
    if len(tokens) > 6:
        return False
    return len(phrase) >= 4


@dataclass(slots=True)
class CandidateEvidence:
    phrase: str
    provider: str
    event_id: str
    explicit: bool = False
    weight: float = 1.0


@dataclass(slots=True)
class CandidateTopic:
    id: str
    name: str
    aliases: list[str]
    discovery_score: float
    provider_count: int
    event_count: int
    evidence_ids: list[str]
    providers: list[str]
    explicit_evidence_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": self.aliases,
            "discovery_score": round(self.discovery_score, 2),
            "provider_count": self.provider_count,
            "event_count": self.event_count,
            "evidence_ids": self.evidence_ids,
            "providers": self.providers,
            "explicit_evidence_count": self.explicit_evidence_count,
        }


def _query_from_gsc(event: RawEvent) -> str | None:
    if event.provider != "gsc" or not isinstance(event.raw, dict):
        return None
    dimensions = event.raw.get("dimensions") or {}
    query = str(dimensions.get("query") or "").strip()
    return query or None


def _github_topics(event: RawEvent) -> list[str]:
    if event.provider != "github" or not isinstance(event.raw, dict):
        return []
    topics = event.raw.get("topics") or []
    return [str(item).replace("-", " ").strip() for item in topics if str(item).strip()]


def _event_scope_tokens(event: RawEvent) -> set[str]:
    parts = [event.title, event.text, event.community]
    if event.provider == "github":
        parts.extend(_github_topics(event))
    return set(canonical_tokens(" ".join(str(part or "") for part in parts)))


# "agentic" 是 AI/Agent 家族的显式语义证据，即使表面 token 不等于 "ai" / "agent"。
# 这里只扩展 AI/Agent 相关 scope，不会让事件对任意 scope 全局放宽。
AGENTIC_TOKENS = {"agentic"}
AGENTIC_SCOPE_TOKENS = {"ai", "agent"}
# 中文说明：AI 是范围词里的“泛词”。当 scope 同时包含更具体的词（如 safety/coding）时，
# 只命中 AI 不能算相关，否则所有 AI 子范围都会退化成同一批泛主题。
GENERIC_AI_SCOPE_TOKENS = {"ai", "artificial", "intelligence"}

# Defined before its first use so scope gating never depends on module-load
# ordering; the Chinese phrase extractor below reuses it.
HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def _scope_supported(event: RawEvent, scope_tokens: set[str]) -> bool:
    if not scope_tokens:
        return True
    event_tokens = _event_scope_tokens(event)
    text = " ".join(filter(None, [event.title, event.text, event.community]))

    specific_tokens = scope_tokens - GENERIC_AI_SCOPE_TOKENS
    if specific_tokens:
        # 中文说明：复合 AI scope 必须命中至少一个具体 token。
        # 例：AI Safety 不能因为事件里只有 AI 就通过；必须同时出现 safety（或受控语义别名）。
        if event_tokens & specific_tokens:
            return True
        # agentic / 智能体 是 agent 范围的受控语义别名，只能替代 agent，不能替代 safety/coding 等其他具体词。
        if "agent" in specific_tokens and event_tokens & AGENTIC_TOKENS:
            return True
        if specific_tokens & {"agent", "智能体"} and "智能体" in text:
            return True
        return any(HAN_RE.search(token) and token in text for token in specific_tokens)

    # 纯泛 AI scope 保持原行为，避免修复子范围时破坏既有 AI / agentic 发现能力。
    if event_tokens & scope_tokens:
        return True
    if event_tokens & AGENTIC_TOKENS and scope_tokens & AGENTIC_SCOPE_TOKENS:
        return True
    if "ai" in scope_tokens and "人工智能" in text:
        return True
    return any(HAN_RE.search(token) and token in text for token in scope_tokens)


# 中文说明：事件相关不代表事件里抽出的每个短语都相关。这里用“范围语义族”
# 做候选级二次门禁，只描述通用概念关系，不维护产品名白名单，也不调用模型。
CANDIDATE_SCOPE_RULES = {
    "coding": {
        "scope": {"coding", "code", "编程", "代码"},
        "tokens": {"code", "coding", "programming", "developer", "development", "software", "engineer", "engineering", "ide", "debug", "debugging", "compiler"},
        "han": ("编程", "代码", "开发", "软件工程", "调试"),
    },
    "evaluation": {
        "scope": {"evaluation", "eval", "评测", "评估"},
        "tokens": {"evaluation", "eval", "benchmark", "judge", "judging", "grader", "grading", "metric", "testing", "test"},
        "han": ("评测", "评估", "基准", "测试", "评分"),
    },
    "infrastructure": {
        "scope": {"infrastructure", "infra", "基础设施"},
        "tokens": {"infrastructure", "infra", "serving", "inference", "deployment", "runtime", "compute", "gpu", "cluster", "hosting"},
        "han": ("基础设施", "推理", "部署", "运行时", "算力", "集群"),
    },
    "safety": {
        "scope": {"safety", "安全"},
        "tokens": {"safety", "alignment", "guardrail", "guardrails", "risk", "security", "secure", "redteam", "red-team"},
        "han": ("安全", "对齐", "护栏", "风险", "红队"),
    },
    "agent": {
        "scope": {"agent", "agentic", "智能体"},
        "tokens": {"agent", "agentic", "multiagent", "orchestration", "orchestrator", "autonomous"},
        "han": ("智能体", "代理编排", "自主代理"),
    },
    "search": {
        "scope": {"search", "搜索", "检索"},
        "tokens": {"retrieval", "retriever", "rag", "vector", "semantic", "embedding", "indexing", "query"},
        "han": ("检索", "向量搜索", "语义搜索", "向量检索", "查询"),
    },
    "productivity": {
        "scope": {"productivity", "生产力", "效率"},
        "tokens": {"productivity", "workflow", "automation", "copilot", "assistant", "efficiency"},
        "han": ("生产力", "效率", "工作流", "自动化", "助手"),
    },
}
CANDIDATE_SCOPE_PRIORITY = ("coding", "evaluation", "infrastructure", "safety", "agent", "search", "productivity")
AI_SEARCH_DOMAIN_ANCHORS = {"ai", "llm", "agent", "agentic", "gpt", "model", "neural"}
AI_SEARCH_AMBIGUOUS_CONTEXT = {"job", "career", "resume", "hiring", "employment", "recruiting"}
# 中文说明：不是产品白名单；只识别“字母标识 + 版本号”这种结构化版本标识，
# 用于保留已被父事件证明属于 AI scope 的 GPT-6.1 这类模型版本候选。
VERSIONED_IDENTIFIER_RE = re.compile(r"\b[a-z][a-z0-9]*[-.]?\d+(?:[.-]\d+)*\b", re.I)


def _candidate_scope_supported(phrase: str, scope_tokens: set[str]) -> bool:
    """候选短语自身必须支持具体 scope；第一方 GSC 在调用方已提前放行。"""
    if not scope_tokens:
        return True
    specific_tokens = scope_tokens - GENERIC_AI_SCOPE_TOKENS
    if not specific_tokens:
        # 纯 AI scope 不加新限制，保持既有泛 AI 发现行为。
        return True

    tokens = set(canonical_tokens(phrase))
    normalized = normalize_text(phrase)
    rule_name = next(
        (
            name
            for name in CANDIDATE_SCOPE_PRIORITY
            if specific_tokens & CANDIDATE_SCOPE_RULES[name]["scope"]
        ),
        None,
    )
    if rule_name is None:
        # 未登记的新 scope 采用保守回退：至少命中具体 scope token / 中文子串。
        return bool(tokens & specific_tokens) or any(
            HAN_RE.search(token) and token in phrase for token in specific_tokens
        )

    rule = CANDIDATE_SCOPE_RULES[rule_name]
    if rule_name == "agent" and VERSIONED_IDENTIFIER_RE.search(phrase):
        # 父事件已经通过 agent/agentic 范围门禁；版本化模型名自身是足够具体的 AI 候选。
        return True
    if rule_name == "search":
        # “search”高度多义：job search / resume search 不能因为父事件属于 AI Search 就泄漏。
        # retrieval/RAG/vector 等是强 AI-search 证据；只有弱词 search 时必须同时有 AI 领域锚点。
        if tokens & AI_SEARCH_AMBIGUOUS_CONTEXT:
            return False
        if tokens & rule["tokens"] or any(marker in normalized for marker in rule["han"]):
            return True
        return "search" in tokens and bool(tokens & AI_SEARCH_DOMAIN_ANCHORS)

    return bool(tokens & rule["tokens"]) or any(marker in normalized for marker in rule["han"])


# Chinese Candidate Discovery V1: conservative observed spans, not translated or
# LLM-invented topics. Split before normalizing so punctuation and verbs remain
# boundaries; never stitch words from unrelated clauses into an n-gram.
# 标点始终是硬边界；动作动词先作为软边界试探，只有当切分没有留下任何可用词条
# 时才回退到整段（见 _chinese_segment_phrases），避免把“智能支持系统”这类含
# 动词字符的真实复合名词误删。
ZH_PUNCT_RE = re.compile(r"[，。！？；：、,\n!?;:（）()“”\"《》]+")
ZH_ACTION_RE = re.compile(
    r"推出|发布|宣布|提供|包含|支持|主打|发现|讲解|如何|"
    r"升至|并呼吁|呼吁|认为|表示|评论|转述|已收到|"
    r"变更后|团队用|团队|入门指南|教程|"
    r"从第一性原理|做了一个|我用|开放预览|实测|等(?=\s*\d)|对(?=\s*[A-Za-z])|"
    r"(?<=\s)评(?=\s)|(?<=\s)在(?=\s)|与|以及"
)
ZH_COUNT_RE = re.compile(r"^\d+(?:\.\d+)?\s*(?:余|多)?\s*[个项种次起条万亿%]")
ZH_GENERIC = {"基准", "模型", "智能体", "ai 智能体", "工具", "更新", "教程", "指南", "发布"}
ZH_INCOMPLETE_RE = re.compile(r"(?:推出|发布|做|审计|以|在|后|将|并|和|与|的)$")
# 回退只接受纯汉字复合词，防止把“NVIDIA 推出了新平台”这类跨语言谓语从句重新
# 拼回候选。
ZH_HAN_COMPOUND_RE = re.compile(r"^[\u4e00-\u9fff]+$")


def _zh_aspect_leak(compound: str) -> bool:
    # 词汇语素 vs 体助词的词法位置判定（非白名单）：了/着/过 充当体助词时是
    # 紧跟动词后的后缀（推出+了、研究+过、看+着），而“过拟合”“着色”中的
    # 过/着 是词首构词成分（前缀/词根）。回退整段里因此只放行词首的 着/过；
    # 了 在该语境下总是谓语后缀，一律按体标记拦截。这样既放行 过拟合/着色
    # 这类真实复合词，又继续挡住“推出了新平台”“研究过支持系统”这类谓语/体泄漏。
    if "了" in compound:
        return True
    return any(char in "着过" for char in compound[1:])


def _clean_zh_chunk(chunk: str) -> str:
    phrase = re.sub(r"\s+", " ", chunk).strip(" -的了")
    # A quantity is evidence about an event, not a discovery topic.
    # Keep the noun span after it only if independently meaningful.
    return ZH_COUNT_RE.sub("", phrase).strip()


def _zh_phrase_ok(phrase: str) -> bool:
    if not phrase or normalize_text(phrase) in ZH_GENERIC or ZH_INCOMPLETE_RE.search(phrase):
        return False
    if not 4 <= len(phrase) <= 48 or re.match(r"^\d", phrase):
        return False
    if HAN_RE.search(phrase):
        if len(HAN_RE.findall(phrase)) > 20:
            return False
    elif len(canonical_tokens(phrase)) < 2 or re.fullmatch(r"\w+\s+AI", phrase, re.I):
        # Single company names are too broad. Versioned model names survive.
        return False
    return _valid_phrase(phrase)


def _chinese_segment_phrases(segment: str) -> list[str]:
    chunks = [_clean_zh_chunk(chunk) for chunk in ZH_ACTION_RE.split(segment)]
    pieces = [chunk for chunk in chunks if _zh_phrase_ok(chunk)]
    compound = _clean_zh_chunk(segment)
    # 覆盖性门禁：切分结果只有在仍覆盖到整段末尾（尾部 chunk 被保留）时才可直接接受。
    # 否则说明切掉了尾部名词，一个左侧长度≥4 的合法前缀片段（如 智能客服）会短路
    # 整段回退，把 智能客服支持平台 截成过于泛化的 智能客服。尾部保留时不得升级为
    # 整段，否则“厂商发布智能体安全平台”这类主谓宾句子会被误当成复合词（回归）。
    tail = next((chunk for chunk in reversed(chunks) if chunk), "")
    if pieces and tail in pieces:
        return pieces
    # 回退：尾部名词被切掉且整段本身是合法纯汉字复合词时保留整段。
    # 边缘动词碎片（如“NVIDIA 推出”“AI 发布”）仍被 ZH_INCOMPLETE_RE / 非纯汉字
    # 条件挡掉，不会借回退泄漏。
    if ZH_HAN_COMPOUND_RE.match(compound):
        # 以动作动词开头的整段是谓语短语（如“推出新平台”），不是复合名词：先剥离
        # 前导动词，只保留剥离后仍独立合法的名词部分，否则整段丢弃。这样既守住
        # “推出/发布”类动作碎片不泄漏，又让“推出多模态支持平台”保留真实名词。
        leading = ZH_ACTION_RE.match(compound)
        if leading:
            compound = compound[leading.end():]
        if not _zh_aspect_leak(compound) and _zh_phrase_ok(compound):
            return [compound]
    # 整段不合格时保留原有局部片段结果（pieces 为空即返回 []），不额外扩大或收紧抑制面。
    return pieces


def _chinese_phrases(text: str, max_phrases: int) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    text = re.sub(r"https?://\S+", "", text)
    for segment in ZH_PUNCT_RE.split(text):
        for phrase in _chinese_segment_phrases(segment):
            key = normalize_text(phrase)
            if key in seen:
                continue
            seen.add(key)
            phrases.append(phrase)
            if len(phrases) >= max_phrases:
                return phrases
    return phrases


def _title_phrases(text: str, max_phrases: int = 20) -> list[str]:
    """Extract deterministic 2-4 token phrases from observed text."""
    raw_tokens = [normalize_text(token) for token in TOKEN_RE.findall(text)]
    filtered = [
        _singular(token)
        for token in raw_tokens
        if token and token not in STOPWORDS
    ]
    if len(filtered) < 2:
        return []

    phrases: list[str] = []
    seen: set[str] = set()
    for size in (2, 3, 4):
        for start in range(0, len(filtered) - size + 1):
            window = filtered[start:start + size]
            content = [token for token in window if token not in GENERIC_SINGLE_TOKENS]
            if not content:
                continue
            phrase = _clean_phrase(window)
            if not _valid_phrase(phrase) or phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
            if len(phrases) >= max_phrases:
                return phrases
    return phrases


def extract_candidate_evidence(
    events: Sequence[RawEvent],
    *,
    scope: str | None = None,
    max_title_phrases_per_event: int = 12,
) -> list[CandidateEvidence]:
    """Generate candidate evidence only from observed source data."""
    evidence: list[CandidateEvidence] = []
    scope_tokens = set(canonical_tokens(scope or ""))

    for event in events:
        query = _query_from_gsc(event)
        if query and _valid_phrase(query, allow_generic=True):
            query_tokens = canonical_tokens(query)
            # 中文说明：第一方 GSC 单 token 查询（如 "python"）是观察到的站内需求，
            # 保留既有逃生通道；多 token 查询必须与其他候选一样独立通过范围门禁，
            # 否则 "job search" / "video production" 会绕过候选级闸门重新泄漏。
            if len(query_tokens) == 1 or _candidate_scope_supported(query, scope_tokens):
                evidence.append(
                    CandidateEvidence(
                        phrase=query,
                        provider=event.provider,
                        event_id=event.id,
                        explicit=True,
                        weight=2.5,
                    )
                )

        scope_supported = _scope_supported(event, scope_tokens)

        for topic in _github_topics(event):
            topic_tokens = canonical_tokens(topic)
            # Public repository tags such as "python", "openai", or "cursor"
            # are too broad/ambiguous to become standalone opportunities.
            # First-party GSC queries remain the explicit single-token escape
            # hatch because they represent observed site demand.
            if len(topic_tokens) == 1:
                continue
            if (
                scope_supported
                and _valid_phrase(topic)
                and _candidate_scope_supported(topic, scope_tokens)
            ):
                evidence.append(
                    CandidateEvidence(
                        phrase=topic,
                        provider=event.provider,
                        event_id=event.id,
                        explicit=True,
                        weight=2.0,
                    )
                )

        source_text = " ".join(filter(None, [event.title, event.text]))
        if not scope_supported:
            continue
        if HAN_RE.search(source_text):
            # Prefer headline spans; summaries can drown a specific topic in
            # repeated attribution, quantities and boilerplate.
            phrases = _chinese_phrases(event.title or "", max_title_phrases_per_event)
            if not phrases:
                phrases = _chinese_phrases(event.text or "", max_title_phrases_per_event)
        else:
            phrases = _title_phrases(source_text, max_title_phrases_per_event)
        for phrase in phrases:
            tokens = set(canonical_tokens(phrase))
            if scope_tokens and tokens and tokens <= scope_tokens:
                continue
            if not _candidate_scope_supported(phrase, scope_tokens):
                continue
            evidence.append(
                CandidateEvidence(
                    phrase=phrase,
                    provider=event.provider,
                    event_id=event.id,
                    explicit=False,
                    weight=1.0,
                )
            )

    return evidence


def _token_set(text: str) -> set[str]:
    return set(canonical_tokens(text))


def alias_similarity(a: str, b: str) -> float:
    left = _token_set(a)
    right = _token_set(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    intersection = len(left & right)
    union = len(left | right)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(left), len(right))
    return max(jaccard, containment * 0.9 if intersection >= 2 else 0.0)


def _cluster_evidence(
    evidence: Sequence[CandidateEvidence],
    *,
    threshold: float = 0.74,
) -> list[list[CandidateEvidence]]:
    ordered = sorted(
        evidence,
        key=lambda row: (-row.weight, -len(canonical_tokens(row.phrase)), row.phrase),
    )
    clusters: list[list[CandidateEvidence]] = []

    for row in ordered:
        best_index: int | None = None
        best_similarity = 0.0
        for index, cluster in enumerate(clusters):
            representative = cluster[0].phrase
            similarity = alias_similarity(row.phrase, representative)
            if similarity >= threshold and similarity > best_similarity:
                best_index = index
                best_similarity = similarity
        if best_index is None:
            clusters.append([row])
        else:
            clusters[best_index].append(row)
    return clusters


def _canonical_name(cluster: Sequence[CandidateEvidence]) -> str:
    stats: dict[str, tuple[float, int, int]] = {}
    display: dict[str, str] = {}
    for row in cluster:
        normalized = normalize_text(row.phrase)
        if not normalized:
            continue
        score, explicit, count = stats.get(normalized, (0.0, 0, 0))
        stats[normalized] = (
            score + row.weight,
            explicit + int(row.explicit),
            count + 1,
        )
        display.setdefault(normalized, row.phrase.strip())

    if not stats:
        return cluster[0].phrase.strip()

    best = max(
        stats,
        key=lambda key: (
            stats[key][1],
            stats[key][0],
            stats[key][2],
            -len(canonical_tokens(key)),
            -len(key),
        ),
    )
    return display[best]


def cluster_candidates(
    evidence: Sequence[CandidateEvidence],
    *,
    threshold: float = 0.74,
    min_event_count: int = 1,
) -> list[CandidateTopic]:
    """Merge aliases and score discovery strength with provider balance."""
    clusters = _cluster_evidence(evidence, threshold=threshold)
    candidates: list[CandidateTopic] = []

    for cluster in clusters:
        providers = sorted({row.provider for row in cluster})
        event_ids = sorted({row.event_id for row in cluster})
        if len(event_ids) < min_event_count:
            continue

        aliases = sorted({row.phrase.strip() for row in cluster if row.phrase.strip()})
        explicit_count = sum(1 for row in cluster if row.explicit)
        total_weight = sum(row.weight for row in cluster)

        breadth = min(1.0, len(providers) / 4.0)
        event_strength = min(1.0, math.log1p(len(event_ids)) / math.log(8.0))
        explicit_strength = min(1.0, explicit_count / 3.0)
        weight_strength = min(1.0, total_weight / 8.0)
        score = clamp(
            45.0 * breadth
            + 25.0 * event_strength
            + 20.0 * explicit_strength
            + 10.0 * weight_strength
        )

        name = _canonical_name(cluster)
        candidates.append(
            CandidateTopic(
                id=topic_id(name),
                name=name,
                aliases=aliases,
                discovery_score=score,
                provider_count=len(providers),
                event_count=len(event_ids),
                evidence_ids=event_ids,
                providers=providers,
                explicit_evidence_count=explicit_count,
            )
        )

    return sorted(
        candidates,
        key=lambda row: (
            -row.discovery_score,
            -row.provider_count,
            -row.event_count,
            row.name,
        ),
    )


def event_candidate_affinity(event: RawEvent, candidate: CandidateTopic) -> float:
    """Measure deterministic evidence affinity for one-topic assignment."""
    haystack = normalize_text(" ".join(filter(None, [event.title, event.text, event.community])))
    hay_tokens = set(canonical_tokens(haystack))
    if not hay_tokens:
        return 0.0

    best = 0.0
    for alias in [candidate.name, *candidate.aliases]:
        alias_norm = normalize_text(alias)
        alias_tokens = set(canonical_tokens(alias))
        if not alias_tokens:
            continue
        if alias_norm and alias_norm in haystack:
            best = max(best, 1.0)
            continue
        overlap = len(alias_tokens & hay_tokens) / len(alias_tokens)
        best = max(best, overlap)
    return best


def assign_events_exclusively(
    events: Sequence[RawEvent],
    candidates: Sequence[CandidateTopic],
    *,
    min_affinity: float = 0.60,
) -> dict[str, list[str]]:
    """Assign each source event to at most one candidate topic."""
    assignments: dict[str, list[str]] = {candidate.id: [] for candidate in candidates}
    for event in events:
        ranked = sorted(
            (
                (event_candidate_affinity(event, candidate), candidate)
                for candidate in candidates
            ),
            key=lambda item: (
                -item[0],
                -item[1].discovery_score,
                item[1].name,
            ),
        )
        if not ranked or ranked[0][0] < min_affinity:
            continue
        assignments[ranked[0][1].id].append(event.id)
    return assignments


def apply_assignment_evidence(
    candidates: Sequence[CandidateTopic],
    assignments: Mapping[str, Sequence[str]],
) -> list[CandidateTopic]:
    rows: list[CandidateTopic] = []
    for candidate in candidates:
        assigned = sorted(set(assignments.get(candidate.id, ())))
        if not assigned:
            continue
        rows.append(
            CandidateTopic(
                id=candidate.id,
                name=candidate.name,
                aliases=candidate.aliases,
                discovery_score=candidate.discovery_score,
                provider_count=candidate.provider_count,
                event_count=len(assigned),
                evidence_ids=assigned,
                providers=candidate.providers,
                explicit_evidence_count=candidate.explicit_evidence_count,
            )
        )
    return rows


def discover_candidates(
    events: Sequence[RawEvent],
    *,
    scope: str | None = None,
    max_candidates: int = 20,
) -> list[CandidateTopic]:
    evidence = extract_candidate_evidence(events, scope=scope)
    clustered = cluster_candidates(evidence)
    assignments = assign_events_exclusively(events, clustered[: max_candidates * 3])
    assigned = apply_assignment_evidence(clustered, assignments)

    by_id = {candidate.id: candidate for candidate in assigned}
    for candidate in clustered:
        if candidate.id in by_id:
            continue
        if candidate.explicit_evidence_count > 0:
            by_id[candidate.id] = candidate

    return sorted(
        by_id.values(),
        key=lambda row: (
            -row.discovery_score,
            -row.provider_count,
            -row.event_count,
            row.name,
        ),
    )[:max_candidates]


def overlap_penalized_rank(
    reports: Sequence[dict],
    *,
    overlap_weight: float = 0.35,
) -> list[dict]:
    """Rank deep scans while penalizing repeated evidence across opportunities."""
    ordered = sorted(
        reports,
        key=lambda row: (
            -float((row.get("opportunity") or {}).get("score") or 0.0),
            str(row.get("topic") or ""),
        ),
    )
    claimed: set[str] = set()
    ranked: list[dict] = []

    for row in ordered:
        opportunity = row.get("opportunity") or {}
        evidence = set(opportunity.get("evidence_ids") or [])
        overlap = len(evidence & claimed) / len(evidence) if evidence else 0.0
        base = float(opportunity.get("score") or 0.0)
        rank_score = clamp(base * (1.0 - overlap_weight * overlap))

        enriched = dict(row)
        enriched["rank_score"] = round(rank_score, 2)
        enriched["evidence_overlap_ratio"] = round(overlap, 4)
        ranked.append(enriched)
        claimed.update(evidence)

    ranked.sort(
        key=lambda row: (
            -float(row.get("rank_score") or 0.0),
            -float((row.get("opportunity") or {}).get("score") or 0.0),
            str(row.get("topic") or ""),
        )
    )
    return ranked
