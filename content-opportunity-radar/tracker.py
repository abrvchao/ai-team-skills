"""Radar V2 持续追踪 —— 持久化层（Milestone 1）。

设计依据：`.ai-team-runs/radar-v2-active/qwen-interface/RADAR_V2_IMPLEMENTATION_PLAN.md`
的 §2「持久化迁移」。

硬约束（不得违反）：
- 只在 `content-opportunity-radar/` 目录内新增文件；不修改 V1 任何源码。
- 不修改 V1 表的 CREATE TABLE 语句，不给 V1 表加列/加索引。
- V2 的 4 张表与 V1 同库（默认 `.radar/radar.db`）、不同表，全部 `CREATE TABLE IF NOT EXISTS`。
- V2 表仅通过 FOREIGN KEY 引用 `workspaces(workspace_id)`；
  刻意**不**引用 V1 的 `radar_runs` / `opportunity_snapshots`，避免级联删除风险。
- 所有 ID 复用 `core.stable_id()`，所有时间戳复用 `core.utcnow()/isoformat()`，保证确定性。

术语对照：
- schedule（调度配置）：某个工作区按 cron 周期触发一次追踪。
- tracker run（追踪执行记录）：一次调度触发的执行流水，关联到一个 V1 radar run。
- diff（变化检测结果）：相邻两次 radar run 之间的差异条目。
- alert（提醒）：由高幅度 diff 派生出的、可供下游消费的通知记录。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from core import isoformat, stable_id, utcnow


# 默认调度周期：每 6 小时整点触发一次。
DEFAULT_CRON = "0 */6 * * *"

# V2 新增的 4 张表 + 索引。与 V1 同库不同表，全部幂等建表。
V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracker_schedules (
    schedule_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    cron_expr TEXT NOT NULL DEFAULT '0 */6 * * *',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_schedules_due
ON tracker_schedules(next_due_at ASC, enabled DESC);

CREATE TABLE IF NOT EXISTS tracker_runs (
    tracker_run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    radar_run_id TEXT,
    scheduled_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT,
    prev_radar_run_id TEXT,
    FOREIGN KEY(schedule_id) REFERENCES tracker_schedules(schedule_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_runs_workspace_time
ON tracker_runs(workspace_id, scheduled_at DESC, tracker_run_id DESC);

CREATE TABLE IF NOT EXISTS tracker_diffs (
    diff_id TEXT PRIMARY KEY,
    tracker_run_id TEXT NOT NULL,
    prev_radar_run_id TEXT,
    curr_radar_run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    change_type TEXT NOT NULL,
    prev_rank INTEGER,
    curr_rank INTEGER,
    prev_score REAL,
    curr_score REAL,
    magnitude REAL NOT NULL DEFAULT 0.0,
    details_json TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL,
    FOREIGN KEY(tracker_run_id) REFERENCES tracker_runs(tracker_run_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_diffs_run
ON tracker_diffs(tracker_run_id, magnitude DESC, change_type);

CREATE INDEX IF NOT EXISTS idx_tracker_diffs_topic
ON tracker_diffs(topic_id, detected_at DESC);

CREATE TABLE IF NOT EXISTS tracker_alerts (
    alert_id TEXT PRIMARY KEY,
    diff_id TEXT NOT NULL,
    tracker_run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(diff_id) REFERENCES tracker_diffs(diff_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_alerts_workspace
ON tracker_alerts(workspace_id, created_at DESC, delivered);
"""

# tracker_runs.status 的合法取值。
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = (RUN_STATUS_RUNNING, RUN_STATUS_COMPLETED, RUN_STATUS_FAILED)

# tracker_alerts.severity 的合法取值。
ALERT_SEVERITIES = ("info", "warning", "critical")


class TrackerStore:
    """追踪持久化层：调度配置 / 执行记录 / 变化结果 / 提醒。

    与 `read_model.OpportunityReadStore` 保持同构的连接约定
    （WAL + 外键约束 + sqlite3.Row），因此两者可以指向同一个数据库文件。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        initialize: bool = True,
        read_only: bool = False,
    ) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            uri = f"file:{self.path.resolve()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True)
            self.conn.execute("PRAGMA query_only=ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path))
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        if initialize:
            if read_only:
                raise ValueError("read-only store cannot initialize schema")
            self._init_schema()

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "TrackerStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        # 全部 IF NOT EXISTS，可重复执行；不触碰 V1 表。
        self.conn.executescript(V2_SCHEMA)
        self.conn.commit()

    def table_names(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]

    # ── tracker_schedules（调度配置）────────────────────────────────────────

    def create_schedule(
        self,
        workspace_id: str,
        *,
        cron_expr: str = DEFAULT_CRON,
        enabled: bool = True,
        next_due_at: str | None = None,
    ) -> dict:
        """创建（或按确定性 ID 幂等复用）一条工作区调度配置。

        首次创建时 `next_due_at` 默认为当前时间，即立刻到期、等待首次 tick。
        """
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if not cron_expr or not str(cron_expr).strip():
            raise ValueError("cron_expr must not be empty")

        now = isoformat(utcnow())
        schedule_id = stable_id("tracker-schedule", workspace_id, cron_expr)
        due = next_due_at or now
        self.conn.execute(
            """
            INSERT INTO tracker_schedules
                (schedule_id, workspace_id, cron_expr, enabled,
                 last_run_at, next_due_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(schedule_id) DO NOTHING
            """,
            (schedule_id, workspace_id, cron_expr, 1 if enabled else 0, due, now, now),
        )
        self.conn.commit()
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM tracker_schedules WHERE schedule_id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown schedule_id: {schedule_id}")
        return _row_to_dict(row)

    def list_schedules(self, *, workspace_id: str | None = None) -> list[dict]:
        if workspace_id is None:
            rows = self.conn.execute(
                "SELECT * FROM tracker_schedules ORDER BY workspace_id, schedule_id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tracker_schedules WHERE workspace_id = ?"
                " ORDER BY schedule_id",
                (workspace_id,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def due_schedules(self, *, now: str | None = None) -> list[dict]:
        """返回「已启用 且 到期时间已过」的调度，按到期时间升序。"""
        moment = now or isoformat(utcnow())
        rows = self.conn.execute(
            """
            SELECT * FROM tracker_schedules
            WHERE enabled = 1 AND next_due_at IS NOT NULL AND next_due_at <= ?
            ORDER BY next_due_at ASC, schedule_id ASC
            """,
            (moment,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> dict:
        now = isoformat(utcnow())
        cursor = self.conn.execute(
            "UPDATE tracker_schedules SET enabled = ?, updated_at = ?"
            " WHERE schedule_id = ?",
            (1 if enabled else 0, now, schedule_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown schedule_id: {schedule_id}")
        self.conn.commit()
        return self.get_schedule(schedule_id)

    def mark_schedule_ran(
        self,
        schedule_id: str,
        *,
        last_run_at: str,
        next_due_at: str,
    ) -> dict:
        """调度执行完毕后推进水位：记录上次执行时间并写入下次到期时间。"""
        cursor = self.conn.execute(
            """
            UPDATE tracker_schedules
            SET last_run_at = ?, next_due_at = ?, updated_at = ?
            WHERE schedule_id = ?
            """,
            (last_run_at, next_due_at, isoformat(utcnow()), schedule_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown schedule_id: {schedule_id}")
        self.conn.commit()
        return self.get_schedule(schedule_id)

    # ── tracker_runs（追踪执行记录）─────────────────────────────────────────

    def start_run(
        self,
        *,
        schedule_id: str,
        workspace_id: str,
        scheduled_at: str,
        prev_radar_run_id: str | None = None,
    ) -> dict:
        """登记一次追踪执行的开始，状态为 running。"""
        now = isoformat(utcnow())
        tracker_run_id = stable_id("tracker-run", schedule_id, scheduled_at)
        self.conn.execute(
            """
            INSERT INTO tracker_runs
                (tracker_run_id, schedule_id, workspace_id, radar_run_id,
                 scheduled_at, started_at, finished_at, status,
                 error_message, prev_radar_run_id)
            VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, NULL, ?)
            ON CONFLICT(tracker_run_id) DO NOTHING
            """,
            (
                tracker_run_id,
                schedule_id,
                workspace_id,
                scheduled_at,
                now,
                RUN_STATUS_RUNNING,
                prev_radar_run_id,
            ),
        )
        self.conn.commit()
        return self.get_run(tracker_run_id)

    def complete_run(self, tracker_run_id: str, *, radar_run_id: str) -> dict:
        """标记执行完成，并回填由 V1 产生的 radar_run_id。"""
        cursor = self.conn.execute(
            """
            UPDATE tracker_runs
            SET status = ?, radar_run_id = ?, finished_at = ?, error_message = NULL
            WHERE tracker_run_id = ?
            """,
            (RUN_STATUS_COMPLETED, radar_run_id, isoformat(utcnow()), tracker_run_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown tracker_run_id: {tracker_run_id}")
        self.conn.commit()
        return self.get_run(tracker_run_id)

    def fail_run(self, tracker_run_id: str, *, error_message: str) -> dict:
        """标记执行失败，错误以结构化 JSON 文本记录（不吞异常）。"""
        payload = error_message
        if not isinstance(error_message, str):
            payload = json.dumps(error_message, ensure_ascii=False, sort_keys=True)
        cursor = self.conn.execute(
            """
            UPDATE tracker_runs
            SET status = ?, error_message = ?, finished_at = ?
            WHERE tracker_run_id = ?
            """,
            (RUN_STATUS_FAILED, payload, isoformat(utcnow()), tracker_run_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown tracker_run_id: {tracker_run_id}")
        self.conn.commit()
        return self.get_run(tracker_run_id)

    def get_run(self, tracker_run_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM tracker_runs WHERE tracker_run_id = ?", (tracker_run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown tracker_run_id: {tracker_run_id}")
        return _row_to_dict(row)

    def list_runs(self, *, workspace_id: str | None = None, limit: int = 50) -> list[dict]:
        if workspace_id is None:
            rows = self.conn.execute(
                "SELECT * FROM tracker_runs ORDER BY scheduled_at DESC, tracker_run_id DESC"
                " LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tracker_runs WHERE workspace_id = ?"
                " ORDER BY scheduled_at DESC, tracker_run_id DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def last_completed_radar_run(self, workspace_id: str) -> str | None:
        """取该工作区最近一次成功执行对应的 radar_run_id（用于变化比较的 prev）。"""
        row = self.conn.execute(
            """
            SELECT radar_run_id FROM tracker_runs
            WHERE workspace_id = ? AND status = ? AND radar_run_id IS NOT NULL
            ORDER BY finished_at DESC, tracker_run_id DESC
            LIMIT 1
            """,
            (workspace_id, RUN_STATUS_COMPLETED),
        ).fetchone()
        return row["radar_run_id"] if row else None

    # ── tracker_diffs（变化检测结果）────────────────────────────────────────

    def record_diffs(
        self,
        *,
        tracker_run_id: str,
        curr_radar_run_id: str,
        workspace_id: str,
        diffs: list[Mapping[str, Any]],
        prev_radar_run_id: str | None = None,
        detected_at: str | None = None,
    ) -> int:
        """批量写入变化条目，返回**新插入**的条数。

        diff_id 采用计划 §4.2 的确定性规则
        `stable_id("tracker-diff", prev_run_id, curr_run_id, topic_id, change_type)`，
        因此重复写入同一条 diff 不会产生重复记录（幂等）。
        """
        moment = detected_at or isoformat(utcnow())
        inserted = 0
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        try:
            for diff in diffs:
                topic_id = str(diff.get("topic_id") or "")
                change_type = str(diff.get("change_type") or "")
                if not topic_id or not change_type:
                    raise ValueError("each diff requires topic_id and change_type")
                # D3 修复：diff_id 必须包含 workspace_id，保证跨工作区隔离。
                # 两个工作区即使复用相同的 radar_run_id + topic_id + change_type，
                # 也不会产生 diff_id 冲突（静默数据丢失）。
                # 向后兼容说明：此变更会改变 diff_id 的哈希值，旧 diff_id 不再匹配；
                # 但 ON CONFLICT(diff_id) DO NOTHING 仍保证幂等，新写入不会覆盖旧数据。
                diff_id = stable_id(
                    "tracker-diff",
                    workspace_id,
                    prev_radar_run_id or "",
                    curr_radar_run_id,
                    topic_id,
                    change_type,
                )
                details = diff.get("details") or {}
                cursor = self.conn.execute(
                    """
                    INSERT INTO tracker_diffs
                        (diff_id, tracker_run_id, prev_radar_run_id, curr_radar_run_id,
                         workspace_id, topic_id, topic_name, change_type,
                         prev_rank, curr_rank, prev_score, curr_score,
                         magnitude, details_json, detected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(diff_id) DO NOTHING
                    """,
                    (
                        diff_id,
                        tracker_run_id,
                        prev_radar_run_id,
                        curr_radar_run_id,
                        workspace_id,
                        topic_id,
                        str(diff.get("topic_name") or topic_id),
                        change_type,
                        diff.get("prev_rank"),
                        diff.get("curr_rank"),
                        diff.get("prev_score"),
                        diff.get("curr_score"),
                        float(diff.get("magnitude") or 0.0),
                        json.dumps(details, ensure_ascii=False, sort_keys=True),
                        moment,
                    ),
                )
                inserted += cursor.rowcount
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted

    def list_diffs(
        self,
        *,
        tracker_run_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """按变化幅度降序返回 diff（magnitude DESC, change_type, topic_id）。"""
        sql = "SELECT * FROM tracker_diffs"
        params: list[Any] = []
        if tracker_run_id is not None:
            sql += " WHERE tracker_run_id = ?"
            params.append(tracker_run_id)
        sql += " ORDER BY magnitude DESC, change_type ASC, topic_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    # ── tracker_alerts（提醒记录）──────────────────────────────────────────

    def create_alerts(
        self,
        *,
        tracker_run_id: str,
        workspace_id: str,
        alerts: list[Mapping[str, Any]],
        created_at: str | None = None,
    ) -> int:
        """批量写入提醒，返回**新插入**条数。

        alert_id = stable_id("tracker-alert", diff_id, alert_type)，
        因此同一条 diff 不会重复产生同类提醒。
        """
        moment = created_at or isoformat(utcnow())
        inserted = 0
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        try:
            for alert in alerts:
                diff_id = str(alert.get("diff_id") or "")
                alert_type = str(alert.get("alert_type") or "")
                if not diff_id or not alert_type:
                    raise ValueError("each alert requires diff_id and alert_type")
                severity = str(alert.get("severity") or "info")
                if severity not in ALERT_SEVERITIES:
                    raise ValueError(f"invalid severity: {severity}")
                alert_id = stable_id("tracker-alert", diff_id, alert_type)
                cursor = self.conn.execute(
                    """
                    INSERT INTO tracker_alerts
                        (alert_id, diff_id, tracker_run_id, workspace_id,
                         alert_type, severity, title, summary,
                         delivered, delivered_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                    ON CONFLICT(alert_id) DO NOTHING
                    """,
                    (
                        alert_id,
                        diff_id,
                        tracker_run_id,
                        workspace_id,
                        alert_type,
                        severity,
                        str(alert.get("title") or ""),
                        str(alert.get("summary") or ""),
                        moment,
                    ),
                )
                inserted += cursor.rowcount
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted

    def list_alerts(
        self,
        *,
        workspace_id: str | None = None,
        undelivered_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if undelivered_only:
            clauses.append("delivered = 0")
        sql = "SELECT * FROM tracker_alerts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, alert_id DESC LIMIT ?"
        params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def mark_alert_delivered(self, alert_id: str) -> dict:
        cursor = self.conn.execute(
            "UPDATE tracker_alerts SET delivered = 1, delivered_at = ?"
            " WHERE alert_id = ?",
            (isoformat(utcnow()), alert_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown alert_id: {alert_id}")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM tracker_alerts WHERE alert_id = ?", (alert_id,)
        ).fetchone()
        return _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}
