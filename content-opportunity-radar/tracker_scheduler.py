"""Radar V2 调度执行引擎（Milestone 2）。

只管理 V2 TrackerStore 中的调度执行记录；实际扫描由外部 callback 注入。
默认没有后台循环，不启动网络采集，不修改任何 V1 表。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from radar_v2_diff import detect_changes
from tracker import TrackerStore
from tracker_alerts import build_alerts_from_diffs


def _utc(value: datetime | str) -> datetime:
    """统一到 UTC，拒绝没有时区的时间，避免不同机器错误触发。"""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("scheduler requires timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _values(field: str, low: int, high: int) -> set[int]:
    """解析 cron 数字段的星号、范围、步长与逗号列表。"""
    values: set[int] = set()
    for token in field.split(","):
        if not token:
            raise ValueError("empty cron component")
        base, sep, step_text = token.partition("/")
        step = int(step_text) if sep else 1
        if step < 1:
            raise ValueError("cron step must be positive")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = int(base)
            end = high if sep else start
        if start < low or end > high or start > end:
            raise ValueError("cron field outside supported range")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """解析 UTC 五段式 cron，最低频率为每小时一次。

    初版只允许分钟字段为单一整数，避免免费额度被分钟级调度耗尽。
    """
    fields = str(expr).strip().split()
    if len(fields) != 5:
        raise ValueError("cron must contain exactly five fields")
    minutes, hours, days, months, weekdays = (
        _values(fields[0], 0, 59),
        _values(fields[1], 0, 23),
        _values(fields[2], 1, 31),
        _values(fields[3], 1, 12),
        _values(fields[4], 0, 7),
    )
    if len(minutes) != 1:
        raise ValueError("sub-hourly cron is disabled for cost safety")
    # 周日可写 0 或 7；内部统一为 0。
    weekdays = {0 if value == 7 else value for value in weekdays}
    return minutes, hours, days, months, weekdays


def next_cron_due(expr: str, *, after: datetime | str) -> str:
    """返回严格晚于 after 的下次 UTC 执行时间（ISO8601）。"""
    minutes, hours, days, months, weekdays = parse_cron(expr)
    now = _utc(after)
    minute = next(iter(minutes))
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(hours=1)
    # 允许 2 月 29 日等低频规则：最多搜索五年。
    for _ in range(5 * 366 * 24):
        day_match = candidate.day in days
        week_match = candidate.isoweekday() % 7 in weekdays
        # 与传统 cron 相同：day-of-month 与 day-of-week 都受限时采用 OR。
        # 否则仅检查受限的一侧。
        # 日字段未受限的判定使用完整数值集合。
        day_wildcard = len(days) == 31
        week_wildcard = len(weekdays) == 7
        if day_wildcard:
            calendar_match = week_match
        elif week_wildcard:
            calendar_match = day_match
        else:
            calendar_match = day_match or week_match
        if (candidate.month in months and candidate.hour in hours
                and calendar_match):
            return candidate.isoformat()
        candidate += timedelta(hours=1)
    raise ValueError("cron has no matching UTC time within five years")


@dataclass(frozen=True)
class ScanResult:
    """只接受完成的 V1 扫描快照；失败/不完整结果严禁计算消失机会。"""
    radar_run_id: str
    current_items: Sequence[Mapping[str, Any]]
    previous_items: Sequence[Mapping[str, Any]] | None
    complete: bool
    # 防止历史回放把错误的一次扫描与当前扫描配对。
    previous_radar_run_id: str | None = None


class TrackerScheduler:
    """一个 tick 处理全部到期调度；支持多进程安全抢占和幂等水位推进。"""

    def __init__(self, store: TrackerStore, scan: Callable[[dict], ScanResult],
                 *, top_n: int = 20) -> None:
        if top_n < 1:
            raise ValueError("top_n must be positive")
        self.store = store
        self.scan = scan
        self.top_n = top_n

    def tick(
        self,
        *,
        now: datetime | str,
        schedule_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict]:
        """对到期任务单次处理；失败会记录 failed，不产生 diff/alert。

        通过注入 scan 回调实现 V1 扫描接线。回调须返回可信完整标记和快照，
        本函数不会自行访问 Provider，也不会把 API 失败误判成机会消失。
        """
        moment = _utc(now).isoformat()
        results: list[dict] = []
        # 中文说明：人工单次运行可包含 disabled schedule；仍严格限定目标 schedule_id。
        for schedule in self.store.due_schedules(
            now=moment,
            schedule_id=schedule_id,
            include_disabled=include_disabled,
        ):
            try:
                next_due = next_cron_due(schedule["cron_expr"], after=moment)
            except (TypeError, ValueError):
                # 不修改非法 schedule，交由管理员修正；不能触发不可控任务。
                results.append({"schedule_id": schedule["schedule_id"],
                                "status": "invalid_schedule"})
                continue
            # 中文说明：claim 与创建 running run 在同一事务中提交，消除后台/手动抢占窗口。
            run = self.store.claim_and_start_run(
                schedule,
                now=moment,
                next_due=next_due,
                include_disabled=include_disabled,
            )
            if run is None:
                continue
            previous_id = run["prev_radar_run_id"]
            try:
                # 中文说明：把 V2 已确认的前序 run 交给扫描适配器，禁止它自行挑选其他工作区历史。
                scan_context = {**schedule, "previous_radar_run_id": previous_id}
                output = self.scan(scan_context)
                if not isinstance(output, ScanResult) or not output.complete:
                    raise ValueError("scan_incomplete")
                if not output.radar_run_id or not output.current_items:
                    raise ValueError("scan_empty_or_missing_run")
                # 首次 V2 执行可从同 workspace 的 V1 历史快照建立基线；之后必须沿用 V2 成功水位。
                if previous_id is None and output.previous_radar_run_id is not None:
                    if output.previous_items is None:
                        raise ValueError("previous_snapshot_missing")
                    previous_id = output.previous_radar_run_id
                    self.store.set_run_previous(
                        run["tracker_run_id"],
                        prev_radar_run_id=previous_id,
                    )
                if previous_id is not None and output.previous_items is None:
                    raise ValueError("previous_snapshot_missing")
                if output.previous_radar_run_id != previous_id:
                    raise ValueError("previous_run_mismatch")
                diffs = detect_changes(
                    output.previous_items or [],
                    output.current_items,
                    prev_run_id=previous_id,
                    curr_run_id=output.radar_run_id,
                    top_n=self.top_n,
                )
                count = self.store.record_diffs(
                    tracker_run_id=run["tracker_run_id"],
                    curr_radar_run_id=output.radar_run_id,
                    prev_radar_run_id=previous_id,
                    workspace_id=schedule["workspace_id"],
                    diffs=diffs,
                )
                # 提醒闭环：将已持久化的 diff 转换为去重 alert。
                # 使用 list_diffs 取回含 diff_id 的完整记录，而非 record_diffs 的返回值（int）。
                persisted_diffs = self.store.list_diffs(
                    tracker_run_id=run["tracker_run_id"],
                )
                alerts = build_alerts_from_diffs(persisted_diffs)
                alert_count = 0
                if alerts:
                    try:
                        alert_count = self.store.create_alerts(
                            tracker_run_id=run["tracker_run_id"],
                            workspace_id=schedule["workspace_id"],
                            alerts=alerts,
                        )
                    except Exception:
                        # alert 持久化失败时回滚该 run 的 alert 行，保持 run 状态一致。
                        self.store.conn.execute(
                            "DELETE FROM tracker_alerts WHERE tracker_run_id = ?",
                            (run["tracker_run_id"],),
                        )
                        self.store.conn.commit()
                        raise
                self.store.complete_run(run["tracker_run_id"],
                                        radar_run_id=output.radar_run_id)
                results.append({"schedule_id": schedule["schedule_id"],
                                "tracker_run_id": run["tracker_run_id"],
                                "status": "completed", "diff_count": count,
                                "alert_count": alert_count})
            except BaseException as exc:
                # 只存错误类型/类别，防止第三方错误包含 Key、Bearer 等敏感文本。
                code = ("scan_incomplete" if str(exc) == "scan_incomplete" else
                        "scan_empty_or_missing_run" if str(exc) == "scan_empty_or_missing_run" else
                        "previous_snapshot_missing" if str(exc) == "previous_snapshot_missing" else
                        "previous_run_mismatch" if str(exc) == "previous_run_mismatch" else
                        "scan_execution_failed")
                # 中文说明：rollback 后用一个事务清理 alert/diff 并标记 failed，外键顺序为子表优先。
                self.store.conn.rollback()
                with self.store.conn:
                    self.store.conn.execute(
                        "DELETE FROM tracker_alerts WHERE tracker_run_id = ?",
                        (run["tracker_run_id"],),
                    )
                    self.store.conn.execute(
                        "DELETE FROM tracker_diffs WHERE tracker_run_id = ?",
                        (run["tracker_run_id"],),
                    )
                    self.store.fail_run(
                        run["tracker_run_id"], error_message=code, commit=False
                    )
                # KeyboardInterrupt/SystemExit 也完成清理，但必须继续向上传播。
                if not isinstance(exc, Exception):
                    raise
                results.append({"schedule_id": schedule["schedule_id"],
                                "tracker_run_id": run["tracker_run_id"],
                                "status": "failed", "error": code})
        return results
