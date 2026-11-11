"""V1 Radar 历史快照的只读回放适配器（Radar V2 Milestone 3）。

设计约束：
- 全程只读 V1 的 radar_runs 与 opportunity_snapshots；
- 只接受显式 run_id + workspace_id + scope，永不偷换成 latest_run；
- 扫描错误、空数据、跨工作区或跨 scope 均拒绝计算 diff；
- 回放现有结果，不调用 provider；正式自动扫描另行接线。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from read_model import OpportunityReadStore
from tracker_scheduler import ScanResult


class HistoryValidationError(ValueError):
    """历史数据无法安全用于机会变化比较。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HistorySnapshot:
    run_id: str
    generated_at: str
    items: list[dict[str, Any]]


class HistoricalRunReader:
    """只读数据库视图；不初始化、更不迁移 V1 表。"""

    def __init__(self, database: str | Path, *, top_n: int = 20) -> None:
        if top_n < 1 or top_n > 100:
            raise ValueError("top_n must be between 1 and 100")
        self.store = OpportunityReadStore(database, read_only=True, initialize=False)
        self.top_n = top_n

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "HistoricalRunReader":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def load(self, *, run_id: str, workspace_id: str, scope: str) -> HistorySnapshot:
        """严格按 run_id 读取一次成功、完整的工作区扫描。"""
        if not run_id or not workspace_id or not scope:
            raise HistoryValidationError("missing_identity")
        run = self.store.run_by_id(run_id)
        if run is None:
            raise HistoryValidationError("run_not_found")
        if run.workspace_id != workspace_id or run.scope != scope:
            raise HistoryValidationError("scope_or_workspace_mismatch")
        if run.scan_errors:
            raise HistoryValidationError("source_scan_errors")
        if run.scanned_count <= 0:
            raise HistoryValidationError("no_scanned_candidates")
        page = self.store.list_opportunities(
            scope=scope,
            workspace_id=workspace_id,
            run_id=run_id,
            limit=self.top_n,
        )
        items = page["items"]
        if not items:
            raise HistoryValidationError("empty_snapshot")
        observed_ids: set[str] = set()
        for expected_rank, row in enumerate(items, start=1):
            if row["run_id"] != run_id or row["workspace_id"] != workspace_id or row["scope"] != scope:
                raise HistoryValidationError("snapshot_identity_mismatch")
            if not row["topic_id"] or row["topic_id"] in observed_ids:
                raise HistoryValidationError("duplicate_or_missing_topic")
            observed_ids.add(row["topic_id"])
            if row["rank"] != expected_rank:
                raise HistoryValidationError("non_contiguous_rank")
        return HistorySnapshot(run_id=run_id, generated_at=run.generated_at, items=items)


class ReplayScanAdapter:
    """把两次已经持久化的 V1 扫描转换为调度器的 ScanResult。

    使用前必须保证当前 Radar 扫描结果已经成功落库。
    回放并非实时 provider 执行：给 Milestone 3 提供可核验的真实数据闭环。
    """

    def __init__(
        self,
        history: HistoricalRunReader,
        *,
        scope: str,
        curr_run_id: str,
        prev_run_id: str | None = None,
    ) -> None:
        self.history = history
        self.scope = scope
        self.curr_run_id = curr_run_id
        self.prev_run_id = prev_run_id

    def __call__(self, schedule: dict[str, Any]) -> ScanResult:
        workspace_id = str(schedule["workspace_id"])
        current = self.history.load(
            run_id=self.curr_run_id, workspace_id=workspace_id, scope=self.scope
        )
        previous = (
            self.history.load(
                run_id=self.prev_run_id, workspace_id=workspace_id, scope=self.scope
            )
            if self.prev_run_id is not None else None
        )
        if previous is not None:
            if previous.run_id == current.run_id or previous.generated_at >= current.generated_at:
                raise HistoryValidationError("invalid_run_order")
        return ScanResult(
            radar_run_id=current.run_id,
            current_items=current.items,
            previous_items=previous.items if previous else None,
            complete=True,
            previous_radar_run_id=previous.run_id if previous else None,
        )
