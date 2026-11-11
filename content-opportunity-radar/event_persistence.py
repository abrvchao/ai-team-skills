"""Radar 持久化写入门禁（合成授权不代表生产发布授权）。"""

from core import RawEvent

PREVIEW_SOURCE = "source_intake_offline_review"


class EventPersistenceDenied(ValueError):
    """拒绝将 offline_preview_only 的事件作为生产数据持久化。"""


def ensure_persistable_event(event: object) -> RawEvent:
    """不能只看调用方自己填写的 persistence_allowed 布尔值。"""
    if not isinstance(event, RawEvent):
        raise EventPersistenceDenied("raw_event_required_for_persistence")
    if event.source == PREVIEW_SOURCE:
        raise EventPersistenceDenied("offline_preview_persistence_forbidden")
    return event
