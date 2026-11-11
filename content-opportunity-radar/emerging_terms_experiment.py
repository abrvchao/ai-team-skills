"""早期新词独立实验：借鉴 new-term-radar 的来源去重/基线思想，不接入主评分。"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from collections import defaultdict
from math import isfinite

@dataclass(frozen=True)
class TermMention:
    term: str
    source_id: str
    day: date

def detect_emerging_terms(mentions: list[TermMention], *, today: date,
                          recent_days: int = 3, baseline_days: int = 30,
                          minimum_sources: int = 2, min_lift: float = 2.0,
                          enabled: bool = False) -> list[dict]:
    """只读取本地传入的来源证据，返回候选；不开关则完全无副作用。"""
    if not enabled:
        return []
    if not (recent_days > 0 and baseline_days > 0 and minimum_sources > 0
            and isfinite(min_lift) and min_lift > 0):
        raise ValueError("invalid_detection_policy")
    recent, baseline = defaultdict(set), defaultdict(set)
    for mention in mentions:
        if not isinstance(mention, TermMention):
            raise ValueError("invalid_mention")
        term = " ".join(mention.term.casefold().split())
        if not term or len(term) > 120 or not mention.source_id:
            continue
        # 只计算独立来源，不允许同一账号/站点刷屏伪造爆发。
        age = (today - mention.day).days
        if 0 <= age < recent_days:
            recent[term].add(mention.source_id)
        elif recent_days <= age < recent_days + baseline_days:
            baseline[term].add(mention.source_id)
    rows = []
    for term, sources in recent.items():
        if len(sources) < minimum_sources:
            continue
        # 平滑常数避免旧词零基线时除零。
        expected = len(baseline[term]) * recent_days / baseline_days
        lift = len(sources) / (expected + 1)
        if lift >= min_lift:
            rows.append({"term": term, "sources": len(sources),
                         "baseline_sources": len(baseline[term]),
                         "lift": round(lift, 2), "status": "experimental"})
    return sorted(rows, key=lambda r: (-r["lift"], -r["sources"], r["term"]))

def settled_trend(points: list[tuple[date, float]], *, today: date,
                  unsettled_days: int = 2) -> list[tuple[date, float]]:
    """去除尚未稳定的最近两天，避免假增长；趋势仅供离线复验。"""
    if unsettled_days < 0:
        raise ValueError("invalid_unsettled_days")
    cutoff = today - timedelta(days=unsettled_days)
    return [(day, value) for day, value in points if day < cutoff]
