"""Minimal topic/entity normalization for Phase 1."""
from __future__ import annotations

import html
import re
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Iterable

from core import RawEvent, stable_id


@lru_cache(maxsize=16384)
def normalize_text(value: str) -> str:
    # 中文说明：这是纯函数，使用有界 LRU cache（最近最少使用缓存）复用重复文本规范化结果。
    # 16384 的上限防止长时间运行时缓存无限增长。
    # 必须先解码 HTML entity（HTML 实体）。否则 `&nbsp;` 会被错误拆成
    # `and nbsp`，并可能在多条 Google News 文本里聚成假的高频主题 `nbsp nbsp`。
    value = html.unescape(value).casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def topic_id(topic: str) -> str:
    normalized = normalize_text(topic)
    slug = re.sub(r"\s+", "-", normalized).strip("-") or "topic"
    return f"topic:{slug}"


@dataclass(slots=True)
class EntityNode:
    id: str
    kind: str
    name: str
    aliases: set[str] = field(default_factory=set)


@dataclass(slots=True)
class TopicNode:
    id: str
    name: str
    aliases: set[str] = field(default_factory=set)


class TopicResolver:
    """Deterministic resolver; no LLM is required to decide topic identity."""

    def __init__(self) -> None:
        self._topics: dict[str, TopicNode] = {}
        self._aliases: dict[str, str] = {}

    def register(self, name: str, aliases: Iterable[str] = ()) -> TopicNode:
        tid = topic_id(name)
        node = TopicNode(tid, name, set(aliases))
        node.aliases.add(name)
        self._topics[tid] = node
        for alias in node.aliases:
            self._aliases[normalize_text(alias)] = tid
        return node

    def resolve(self, text: str) -> TopicNode:
        normalized = normalize_text(text)
        if normalized in self._aliases:
            return self._topics[self._aliases[normalized]]
        return self.register(text)

    def match_event(self, event: RawEvent, topic: TopicNode) -> bool:
        haystack = normalize_text(" ".join(filter(None, [event.title, event.text, event.community])))
        terms = {normalize_text(topic.name), *(normalize_text(a) for a in topic.aliases)}
        return any(term and term in haystack for term in terms)

    def event_entities(self, event: RawEvent) -> list[str]:
        entities: list[str] = []
        if event.community:
            entities.append(f"community:{stable_id(event.community)}")
        if event.author:
            entities.append(f"author:{stable_id(event.author)}")
        if event.url and "github.com/" in event.url:
            entities.append(f"repository:{stable_id(event.url)}")
        return entities


def default_resolver() -> TopicResolver:
    resolver = TopicResolver()
    resolver.register(
        "AI Agents",
        aliases=[
            "AI Agent",
            "AI Agents",
            "Agentic AI",
            "AI agent framework",
            "autonomous agents",
            "LLM agents",
        ],
    )
    return resolver
