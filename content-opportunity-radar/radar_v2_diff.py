"""Radar V2 变化检测器（deterministic change detector）。

职责：比较同一 workspace 相邻两次 Radar run 的机会快照，输出可回放、可验收的 diff 列表。
依据：`RADAR_V2_DESIGN.md` 第 5.2 节 ChangeDetector。

边界（truth-safety）：
- 本模块只做纯内存计算，不依赖 V1 代码、不连接数据库、不写入任何 V1 表；
- 不使用随机数、时间戳或字典遍历顺序，因此相同输入必然产生相同输出；
- 输入为普通 ``Mapping`` 列表（可直接来自 V1 ``list_opportunities()["items"]``）。

变化类型：
- ``new`` / ``removed``：进入或离开 top-N 窗口；
- ``rank_up`` / ``rank_down``：名次变化绝对值 >= ``rank_threshold``；
- ``score_up`` / ``score_down``：opportunity 分数变化绝对值 >= ``score_threshold``；
- ``duplicate_id``：同一侧快照内 topic_id 重复（数据完整性告警，不属于机会变化）。

中文说明：``removed`` 即设计文档中的 ``disappeared``，保留别名以兼容文档术语。
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

# 变化类型常量。字符串值即写库/对外合同值，保持英文以便下游消费。
NEW = "new"
REMOVED = "removed"
DISAPPEARED = REMOVED  # 别名：与设计文档术语 disappeared 保持一致
RANK_UP = "rank_up"
RANK_DOWN = "rank_down"
SCORE_UP = "score_up"
SCORE_DOWN = "score_down"
DUPLICATE_ID = "duplicate_id"

CHANGE_TYPES = (
    NEW,
    REMOVED,
    RANK_UP,
    RANK_DOWN,
    SCORE_UP,
    SCORE_DOWN,
    DUPLICATE_ID,
)

# 排序优先级只用于「同等 magnitude」时的稳定顺序，保证 diff 列表可逐字节回放。
_ORDER = {change_type: index for index, change_type in enumerate(CHANGE_TYPES)}

DEFAULT_TOP_N = 20
DEFAULT_RANK_THRESHOLD = 2
DEFAULT_SCORE_THRESHOLD = 5.0
# 设计文档 magnitude 公式：rank_delta / top_n * 50。集中为常量便于解释与测试。
DEFAULT_RANK_SCALE = 50.0
# 出现 / 消失属于最大幅度事件；首次运行（bootstrap）例外，见 detect_changes()。
_APPEARANCE_MAGNITUDE = 100.0


def stable_id(*parts: object) -> str:
    """与 V1 ``core.stable_id`` 完全一致的确定性 ID。

    算法：对各部分做 ``str(p or "")``，用 ``\\x1f`` 连接后取 sha256 前 24 位。
    复刻而非导入，保证本模块可独立运行且不触碰 V1 代码；相同输入与 V1 生成相同 diff_id。
    """
    raw = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _topic_id(item: Mapping[str, Any]) -> str:
    topic_id = item.get("topic_id")
    if topic_id is None:
        topic_id = item.get("id")
    if topic_id is None or str(topic_id) == "":
        raise ValueError("opportunity item is missing topic_id")
    return str(topic_id)


def _topic_name(item: Mapping[str, Any], topic_id: str) -> str:
    name = item.get("topic_name")
    if name in (None, ""):
        name = item.get("topic")
    return str(name) if name not in (None, "") else topic_id


def _rank(item: Mapping[str, Any]) -> int:
    rank = item.get("rank")
    if rank is None:
        raise ValueError("opportunity item %r is missing rank" % (_topic_id(item),))
    return int(rank)


def _score(item: Mapping[str, Any]) -> float | None:
    """兼容 V1 嵌套结构 ``scores.opportunity`` 与扁平 ``opportunity_score``。"""
    if item.get("opportunity_score") is not None:
        return float(item["opportunity_score"])
    scores = item.get("scores")
    if isinstance(scores, Mapping) and scores.get("opportunity") is not None:
        return float(scores["opportunity"])
    if item.get("score") is not None:
        return float(item["score"])
    return None


def _canonicalize(
    items: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """把原始快照整理成 ``topic_id -> record``，并报告重复 ID。

    重复处理（确定性策略）：
    - 每个 topic_id 只保留一条记录，规则为 rank 最小（名次最好）→ 出现顺序最早；
    - 其余行不参与 diff 主流程，但通过 ``duplicate_id`` diff 明确暴露，避免静默丢数据；
    - 遍历顺序不影响结果：最终按 topic_id 排序构造，重复行按 (rank, 出现顺序) 排序。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(items):
        topic_id = _topic_id(item)
        grouped.setdefault(topic_id, []).append(
            {
                "topic_id": topic_id,
                "topic_name": _topic_name(item, topic_id),
                "rank": _rank(item),
                "score": _score(item),
                "_index": index,
            }
        )

    records: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for topic_id in sorted(grouped):
        rows = sorted(grouped[topic_id], key=lambda row: (row["rank"], row["_index"]))
        records[topic_id] = rows[0]
        if len(rows) > 1:
            duplicates.append(
                {
                    "topic_id": topic_id,
                    "topic_name": rows[0]["topic_name"],
                    "kept": rows[0],
                    "dropped": rows[1:],
                }
            )
    return records, duplicates


def _window(records: Mapping[str, dict[str, Any]], top_n: int) -> dict[str, dict[str, Any]]:
    """按 (rank 升序, topic_id 升序) 取前 top_n，返回 topic_id -> record。"""
    ordered = sorted(records.values(), key=lambda row: (row["rank"], row["topic_id"]))
    return {row["topic_id"]: row for row in ordered[:top_n]}


def _make_diff(
    *,
    change_type: str,
    topic_id: str,
    topic_name: str,
    prev_rank: int | None,
    curr_rank: int | None,
    prev_score: float | None,
    curr_score: float | None,
    magnitude: float,
    prev_run_id: str | None,
    curr_run_id: str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rank_delta = None
    if prev_rank is not None and curr_rank is not None:
        rank_delta = prev_rank - curr_rank  # 正数 = 名次上升
    score_delta = None
    if prev_score is not None and curr_score is not None:
        score_delta = round(curr_score - prev_score, 6)

    return {
        "diff_id": stable_id("tracker-diff", prev_run_id, curr_run_id, topic_id, change_type),
        "change_type": change_type,
        "topic_id": topic_id,
        "topic_name": topic_name,
        "prev_rank": prev_rank,
        "curr_rank": curr_rank,
        "prev_score": prev_score,
        "curr_score": curr_score,
        "rank_delta": rank_delta,
        "score_delta": score_delta,
        "magnitude": round(float(magnitude), 6),
        "details": details or {},
    }


def _sort_key(diff: Mapping[str, Any]) -> tuple[Any, ...]:
    """总序排序键：幅度降序 → 类型优先级 → topic_id → 名次 → diff_id。

    最后以唯一 diff_id 兜底，确保不存在并列歧义，列表可逐字节回放。
    """
    return (
        -float(diff["magnitude"]),
        _ORDER[str(diff["change_type"])],
        str(diff["topic_id"]),
        diff["prev_rank"] if diff["prev_rank"] is not None else -1,
        diff["curr_rank"] if diff["curr_rank"] is not None else -1,
        str(diff["diff_id"]),
    )


def detect_changes(
    prev_items: Sequence[Mapping[str, Any]],
    curr_items: Sequence[Mapping[str, Any]],
    *,
    top_n: int = DEFAULT_TOP_N,
    rank_threshold: int = DEFAULT_RANK_THRESHOLD,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    rank_scale: float = DEFAULT_RANK_SCALE,
    prev_run_id: str | None = None,
    curr_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """比较两次 run 的机会快照，返回确定性排序的 diff 列表。

    参数：
    - ``prev_items`` / ``curr_items``：V1 ``list_opportunities()["items"]`` 形态的列表。
    - ``top_n``：只比较前 N 名（设计文档要求 20，而非 V1 默认 5）。
    - ``prev_run_id is None``：视为首次运行（bootstrap），所有机会标记为 ``new`` 且
      ``magnitude=0``，避免首轮刷屏告警；这是设计文档明确的例外。

    ``magnitude`` 计算：
    - ``new`` / ``removed``：100.0（出现/消失为最大变化）；
    - 名次变化：``|rank_delta| / top_n * rank_scale``，上限 100；
    - 分数变化：``|score_delta|``，上限 100；
    - 同一 topic 可同时产生 rank 与 score 两类 diff（设计文档第 5.2 节）。

    重复 ID：不静默丢弃，而是产出 ``duplicate_id`` diff（magnitude=0），并在 details 中
    记录被判重保留的行与被丢弃的行，保证可审计。
    """
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if rank_threshold < 0:
        raise ValueError("rank_threshold must be >= 0")
    if score_threshold < 0:
        raise ValueError("score_threshold must be >= 0")
    if rank_scale < 0:
        raise ValueError("rank_scale must be >= 0")

    prev_records, prev_duplicates = _canonicalize(prev_items)
    curr_records, curr_duplicates = _canonicalize(curr_items)
    prev_window = _window(prev_records, top_n)
    curr_window = _window(curr_records, top_n)
    bootstrap = prev_run_id is None

    diffs: list[dict[str, Any]] = []

    for topic_id in sorted(set(prev_window) | set(curr_window)):
        prev_row = prev_window.get(topic_id)
        curr_row = curr_window.get(topic_id)

        if curr_row is not None and prev_row is None:
            diffs.append(
                _make_diff(
                    change_type=NEW,
                    topic_id=topic_id,
                    topic_name=curr_row["topic_name"],
                    prev_rank=None,
                    curr_rank=curr_row["rank"],
                    prev_score=None,
                    curr_score=curr_row["score"],
                    magnitude=0.0 if bootstrap else _APPEARANCE_MAGNITUDE,
                    prev_run_id=prev_run_id,
                    curr_run_id=curr_run_id,
                    details={"bootstrap": bootstrap},
                )
            )
            continue

        if prev_row is not None and curr_row is None:
            diffs.append(
                _make_diff(
                    change_type=REMOVED,
                    topic_id=topic_id,
                    topic_name=prev_row["topic_name"],
                    prev_rank=prev_row["rank"],
                    curr_rank=None,
                    prev_score=prev_row["score"],
                    curr_score=None,
                    magnitude=_APPEARANCE_MAGNITUDE,
                    prev_run_id=prev_run_id,
                    curr_run_id=curr_run_id,
                )
            )
            continue

        # 两侧都存在：分别独立检测名次变化与分数漂移。
        assert prev_row is not None and curr_row is not None
        rank_delta = prev_row["rank"] - curr_row["rank"]
        if rank_delta != 0 and abs(rank_delta) >= rank_threshold:
            magnitude = min(100.0, abs(rank_delta) / top_n * rank_scale)
            diffs.append(
                _make_diff(
                    change_type=RANK_UP if rank_delta > 0 else RANK_DOWN,
                    topic_id=topic_id,
                    topic_name=curr_row["topic_name"],
                    prev_rank=prev_row["rank"],
                    curr_rank=curr_row["rank"],
                    prev_score=prev_row["score"],
                    curr_score=curr_row["score"],
                    magnitude=magnitude,
                    prev_run_id=prev_run_id,
                    curr_run_id=curr_run_id,
                )
            )

        if prev_row["score"] is not None and curr_row["score"] is not None:
            score_delta = curr_row["score"] - prev_row["score"]
            if abs(score_delta) >= score_threshold:
                diffs.append(
                    _make_diff(
                        change_type=SCORE_UP if score_delta > 0 else SCORE_DOWN,
                        topic_id=topic_id,
                        topic_name=curr_row["topic_name"],
                        prev_rank=prev_row["rank"],
                        curr_rank=curr_row["rank"],
                        prev_score=prev_row["score"],
                        curr_score=curr_row["score"],
                        magnitude=min(100.0, abs(score_delta)),
                        prev_run_id=prev_run_id,
                        curr_run_id=curr_run_id,
                    )
                )

    # 重复 ID 单独成 diff，作为数据完整性信号；两侧都扫描，便于定位是哪一次 run 脏。
    for side, duplicates in (("prev", prev_duplicates), ("curr", curr_duplicates)):
        for duplicate in duplicates:
            topic_id = duplicate["topic_id"]
            prev_row = prev_window.get(topic_id)
            curr_row = curr_window.get(topic_id)
            kept = duplicate["kept"]
            diffs.append(
                _make_diff(
                    change_type=DUPLICATE_ID,
                    topic_id=topic_id,
                    topic_name=duplicate["topic_name"],
                    prev_rank=prev_row["rank"] if prev_row else None,
                    curr_rank=curr_row["rank"] if curr_row else None,
                    prev_score=prev_row["score"] if prev_row else None,
                    curr_score=curr_row["score"] if curr_row else None,
                    magnitude=0.0,
                    prev_run_id=prev_run_id,
                    curr_run_id=curr_run_id,
                    details={
                        "side": side,
                        "duplicate_count": len(duplicate["dropped"]) + 1,
                        "kept_rank": kept["rank"],
                        "dropped_ranks": [row["rank"] for row in duplicate["dropped"]],
                    },
                )
            )

    diffs.sort(key=_sort_key)
    return diffs
