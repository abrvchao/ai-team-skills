"""Radar V2 提醒生成器 —— 将 tracker_diffs 转换为去重提醒（Milestone 2）。

职责：
- 读取一次 tracker run 的 diff 列表，按 change_type 映射为可读的 alert；
- 调用已有的 ``TrackerStore.create_alerts`` 持久化，利用其 ``ON CONFLICT DO NOTHING``
  实现幂等（同一 diff + alert_type 不会重复入库）。

硬约束：
- 不修改生产数据库 schema、不调用付费 API、不发送第三方通知；
- 不重新实现 ``TrackerStore.create_alerts`` 的去重逻辑；
- ``removed`` 类型提醒必须使用「退出当前 Top-N 监控范围」，
  禁止使用「消失」「全网」「下架」等夸大用语。

术语对照（中文备注）：
- alert_type（提醒类型）：与 diff.change_type 一一对应，加 ``_alert`` 后缀区分；
- severity（严重级别）：info / warning / critical，由 magnitude 阈值决定。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from radar_v2_diff import (
    DUPLICATE_ID,
    NEW,
    RANK_DOWN,
    RANK_UP,
    REMOVED,
    SCORE_DOWN,
    SCORE_UP,
)

# ── 严重级别阈值（magnitude 区间 → severity）────────────────────────────
# 这些阈值与 V1 的 opportunity_score 无关，仅用于 V2 alert 分级。
_CRITICAL_THRESHOLD = 80.0
_WARNING_THRESHOLD = 30.0


def _severity_for(magnitude: float) -> str:
    """根据变化幅度返回提醒级别。"""
    if magnitude >= _CRITICAL_THRESHOLD:
        return "critical"
    if magnitude >= _WARNING_THRESHOLD:
        return "warning"
    return "info"


# ── 文案生成（禁止夸大用语）──────────────────────────────────────────────
# 中文硬编码文案：这些字符串直接面向终端用户，必须准确、不夸大。

def _title_for(change_type: str, topic_name: str, diff: Mapping[str, Any]) -> str:
    """生成提醒标题；``removed`` 严格使用「退出当前 Top-N 监控范围」。"""
    if change_type == NEW:
        return f"新机会进入 Top-N：{topic_name}"
    if change_type == REMOVED:
        # 关键约束：不得说「消失」「全网消失」「下架」，只说退出监控范围。
        return f"退出当前 Top-N 监控范围：{topic_name}"
    if change_type == RANK_UP:
        prev = diff.get("prev_rank")
        curr = diff.get("curr_rank")
        return f"排名上升 #{prev}→#{curr}：{topic_name}"
    if change_type == RANK_DOWN:
        prev = diff.get("prev_rank")
        curr = diff.get("curr_rank")
        return f"排名下降 #{prev}→#{curr}：{topic_name}"
    if change_type == SCORE_UP:
        delta = diff.get("score_delta")
        delta_str = f"+{delta:.1f}" if delta is not None else "↑"
        return f"分数上升 {delta_str}：{topic_name}"
    if change_type == SCORE_DOWN:
        delta = diff.get("score_delta")
        delta_str = f"{delta:.1f}" if delta is not None else "↓"
        return f"分数下降 {delta_str}：{topic_name}"
    if change_type == DUPLICATE_ID:
        return f"数据重复告警：{topic_name}"
    return f"变化提醒：{topic_name}"


def _summary_for(change_type: str, topic_name: str, diff: Mapping[str, Any]) -> str:
    """生成提醒摘要（summary 字段，DB schema 要求 NOT NULL）。"""
    magnitude = float(diff.get("magnitude") or 0.0)
    if change_type == NEW:
        curr_rank = diff.get("curr_rank", "?")
        return f"主题「{topic_name}」首次进入 Top-N 监控，当前排名 #{curr_rank}，幅度 {magnitude:.1f}。"
    if change_type == REMOVED:
        prev_rank = diff.get("prev_rank", "?")
        return (
            f"主题「{topic_name}」退出当前 Top-N 监控范围（上次排名 #{prev_rank}），"
            f"幅度 {magnitude:.1f}。可能因排名滑出窗口或数据源更新。"
        )
    if change_type == RANK_UP:
        return (
            f"主题「{topic_name}」排名从 #{diff.get('prev_rank')} 升至 #{diff.get('curr_rank')}，"
            f"幅度 {magnitude:.1f}。"
        )
    if change_type == RANK_DOWN:
        return (
            f"主题「{topic_name}」排名从 #{diff.get('prev_rank')} 降至 #{diff.get('curr_rank')}，"
            f"幅度 {magnitude:.1f}。"
        )
    if change_type == SCORE_UP:
        return (
            f"主题「{topic_name}」分数变化 {diff.get('score_delta', '?')}，"
            f"幅度 {magnitude:.1f}。"
        )
    if change_type == SCORE_DOWN:
        return (
            f"主题「{topic_name}」分数变化 {diff.get('score_delta', '?')}，"
            f"幅度 {magnitude:.1f}。"
        )
    if change_type == DUPLICATE_ID:
        details = diff.get("details") or {}
        count = details.get("duplicate_count", "?")
        side = details.get("side", "?")
        return f"主题「{topic_name}」在 {side} 侧快照中出现 {count} 条重复记录，数据完整性需检查。"
    return f"主题「{topic_name}」发生变化，类型 {change_type}，幅度 {magnitude:.1f}。"


# ── 公开接口 ──────────────────────────────────────────────────────────────

def build_alerts_from_diffs(diffs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """将 diff 列表转换为 ``create_alerts`` 所需的 alert 字典列表。

    每条 diff 生成恰好一条 alert；alert_type = change_type（保持 1:1 映射，
    便于下游按类型过滤）。去重由 ``TrackerStore.create_alerts`` 的
    ``stable_id("tracker-alert", diff_id, alert_type) + ON CONFLICT`` 保证。

    参数：
    - ``diffs``：``TrackerStore.list_diffs()`` 或 ``detect_changes()`` 返回的字典列表，
      每条必须包含 ``diff_id``、``change_type``、``topic_name``。
    """
    alerts: list[dict[str, Any]] = []
    for diff in diffs:
        diff_id = str(diff.get("diff_id") or "")
        change_type = str(diff.get("change_type") or "")
        topic_name = str(diff.get("topic_name") or diff.get("topic_id") or "unknown")
        if not diff_id or not change_type:
            continue
        magnitude = float(diff.get("magnitude") or 0.0)
        alerts.append({
            "diff_id": diff_id,
            "alert_type": change_type,
            "severity": _severity_for(magnitude),
            "title": _title_for(change_type, topic_name, diff),
            "summary": _summary_for(change_type, topic_name, diff),
        })
    return alerts


def validate_alert_copywriting(alerts: Sequence[Mapping[str, Any]]) -> list[str]:
    """检查提醒文案是否包含禁止的夸大用语，返回违规列表。

    禁止词：「消失」「全网」「下架」。
    用于测试断言和上线前检查。
    """
    forbidden = ("消失", "全网", "下架")
    violations: list[str] = []
    for alert in alerts:
        text = f"{alert.get('title', '')} {alert.get('summary', '')}"
        for word in forbidden:
            if word in text:
                violations.append(
                    f"alert diff_id={alert.get('diff_id', '?')} "
                    f"contains forbidden word 「{word}」"
                )
    return violations
