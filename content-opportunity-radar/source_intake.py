"""Radar multi-platform intake staging gate (offline, no network calls).

中文说明：只接收已授权且费用已确认的采集结果；本模块只生成暂存封装，
不会请求平台 API，也不会把手机截图伪装成 RawEvent 的 HTML 采集。
后续经人工审查的 adapter 才能转换成 core.RawEvent。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
import json

PLATFORMS = frozenset({"zhihu", "douyin", "bilibili", "xiaohongshu", "xianyu"})
PATHWAYS = frozenset({"official_api", "oauth_api", "licensed", "android_screen"})
ALLOWED_METRICS = frozenset({
    "mention_count", "new_content_count", "engagement", "engagement_velocity",
    "creator_count", "creator_diversity", "question_count", "pain_count",
    "comparison_count", "content_supply", "content_velocity", "search_rank", "hot_rank",
})
HOSTS = {
    "zhihu": ("zhihu.com",),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "bilibili": ("bilibili.com",),
    "xiaohongshu": ("xiaohongshu.com",),
    "xianyu": ("goofish.com", "2.taobao.com", "idlefish.com"),
}
# 仅开放少量明确定义的字段。任何原始响应、账号凭据和手机设备标识不得进入暂存。
INPUT_FIELDS = frozenset({
    "platform", "source_id", "title", "captured_at", "canonical_url", "metrics",
})
FUTURE_SKEW = timedelta(minutes=5)


class SourceGateError(ValueError):
    """明确拒收；不记录包含账号、请求正文或 URL 查询参数的错误文本。"""


@dataclass(frozen=True, slots=True)
class SourceGrant:
    platform: str
    pathway: str
    grant_ref: str
    expires_at: datetime
    enabled: bool
    price_state: str = "unknown"  # verified_free / user_approved_paid / unknown
    user_approved_charge: bool = False

    def validate(self, now: datetime) -> None:
        if self.platform not in PLATFORMS or self.pathway not in PATHWAYS:
            raise SourceGateError("unsupported_grant")
        if not self.enabled or not self.grant_ref or len(self.grant_ref) > 100:
            raise SourceGateError("missing_or_disabled_authorization")
        if self.expires_at.tzinfo is None or self.expires_at <= now:
            raise SourceGateError("authorization_expired")
        if self.pathway != "android_screen":
            if self.price_state == "verified_free":
                return
            if self.price_state == "user_approved_paid" and self.user_approved_charge:
                return
            raise SourceGateError("unknown_or_unapproved_api_cost")


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise SourceGateError("missing_timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceGateError("invalid_timestamp") from error
    if dt.tzinfo is None:
        raise SourceGateError("timezone_required")
    return dt.astimezone(timezone.utc)


def _url(platform: str, raw: object) -> str | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or len(raw) > 2048:
        raise SourceGateError("invalid_public_url")
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not any(host == suffix or host.endswith("." + suffix)
                           for suffix in HOSTS[platform])):
            raise SourceGateError("invalid_public_url")
    except ValueError as error:
        raise SourceGateError("invalid_public_url") from error
    # 查询串和 fragment 可能包含账号或跟踪凭据，永不保留。
    return urlunsplit(("https", host, parsed.path or "/", "", ""))


def accept_candidate(
    candidate: Mapping[str, object],
    grant: SourceGrant,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """转换成可追溯的暂存事件（非生产 RawEvent）。不读取手机或发网络请求。"""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise SourceGateError("current_time_must_have_timezone")
    current = current.astimezone(timezone.utc)
    if not isinstance(candidate, Mapping) or set(candidate) - INPUT_FIELDS:
        raise SourceGateError("unexpected_fields_rejected")
    platform = candidate.get("platform")
    if platform not in PLATFORMS or platform != grant.platform:
        raise SourceGateError("platform_grant_mismatch")
    grant.validate(current)
    external_id = candidate.get("source_id")
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 180:
        raise SourceGateError("invalid_source_id")
    captured = _time(candidate.get("captured_at"))
    if captured > current + FUTURE_SKEW:
        raise SourceGateError("future_capture_timestamp")
    title = candidate.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 240):
        raise SourceGateError("invalid_title")
    metrics = candidate.get("metrics") or {}
    if not isinstance(metrics, Mapping) or set(metrics) - ALLOWED_METRICS:
        raise SourceGateError("invalid_metric_names")
    numeric: dict[str, float] = {}
    for key, raw in metrics.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SourceGateError("invalid_metric_value")
        n = float(raw)
        if not isfinite(n) or n < 0:
            raise SourceGateError("invalid_metric_value")
        numeric[str(key)] = n
    url = _url(str(platform), candidate.get("canonical_url"))
    # 同来源同条目视为同一采集对象；跨平台数据不能错误去重。
    identity = json.dumps([platform, grant.pathway, external_id.strip()],
                          ensure_ascii=False, separators=(",", ":"))
    dedupe_key = sha256(identity.encode("utf-8")).hexdigest()[:32]
    evidence = {
        "platform": platform, "source_id": external_id.strip(),
        "captured_at": captured.isoformat(), "canonical_url": url,
        "title": title, "metrics": dict(sorted(numeric.items())),
        "pathway": grant.pathway, "grant_ref": grant.grant_ref,
    }
    evidence_hash = sha256(json.dumps(
        evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {**evidence, "dedupe_key": dedupe_key, "evidence_sha256": evidence_hash,
            "status": "staged_only", "is_production_raw_event": False}
