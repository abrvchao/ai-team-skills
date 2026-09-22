"""Candidate discovery and clustering for Opportunity Discovery Loop V1.

This module never invents topics with an LLM. Candidate topics are extracted
from observed source text, Search Console queries, and GitHub repository topics.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
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


def canonical_tokens(text: str) -> tuple[str, ...]:
    tokens = []
    for raw in TOKEN_RE.findall(text):
        token = _singular(normalize_text(raw))
        if not token or token in STOPWORDS:
            continue
        tokens.append(token)
    return tuple(tokens)


def _clean_phrase(tokens: Sequence[str]) -> str:
    return " ".join(token for token in tokens if token).strip()


def _valid_phrase(phrase: str) -> bool:
    tokens = canonical_tokens(phrase)
    if not tokens:
        return False
    if len(tokens) == 1 and tokens[0] in GENERIC_SINGLE_TOKENS:
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
        if query and _valid_phrase(query):
            evidence.append(
                CandidateEvidence(
                    phrase=query,
                    provider=event.provider,
                    event_id=event.id,
                    explicit=True,
                    weight=2.5,
                )
            )

        for topic in _github_topics(event):
            if _valid_phrase(topic):
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
        for phrase in _title_phrases(source_text, max_title_phrases_per_event):
            tokens = set(canonical_tokens(phrase))
            if scope_tokens and tokens and tokens <= scope_tokens:
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
