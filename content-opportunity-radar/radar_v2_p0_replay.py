"""Radar V2 P0：使用已有真实 V1 扫描进行零 API 费用的离线追踪回放。

输入源数据库严格只读；把同范围最近两次完整的 legacy 扫描复制到新的隔离数据库，
再通过 WorkspaceStore / HistoricalRunReader / TrackerScheduler 产生真实的 V2 diff。
这是“历史回放”，**不是**两次实时 provider 采集，更不会启动网络请求。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from core import stable_id
from read_model import OpportunityReadStore
from tracker import TrackerStore
from tracker_history import HistoricalRunReader, ReplayScanAdapter
from tracker_scheduler import TrackerScheduler, _utc
from workspace import WorkspaceStore


_RUN_COLUMNS = (
    "run_id", "workspace_id", "scope", "generated_at", "seed_source_mode",
    "candidate_count", "scanned_count", "warning_json", "error_json",
)
_SNAPSHOT_COLUMNS = (
    "snapshot_id", "run_id", "workspace_id", "scope", "topic_id", "topic_name",
    "rank", "discovery_score", "opportunity_score", "rank_score", "confidence",
    "freshness", "components_json", "reasons_json", "features_json",
    "discovery_json", "evidence_ids_json", "research_pack_json", "created_at",
)


def _digest(path: Path) -> str:
    """只校验输入数据库的主文件字节，不对有并发 WAL 的活库宣称完整快照哈希。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pick_runs(source: sqlite3.Connection, scope: str) -> list[sqlite3.Row]:
    """严格选同一 scope 的最近两次可核查历史记录；不伪造任何源数据。"""
    candidates = source.execute(
        "SELECT * FROM radar_runs WHERE scope = ? AND "
        "COALESCE(workspace_id, '') = '' "
        "ORDER BY generated_at DESC, run_id DESC",
        (scope,),
    ).fetchall()
    valid: list[sqlite3.Row] = []
    for row in candidates:
        if row["scanned_count"] <= 0:
            continue
        if json.loads(row["error_json"] or "[]"):
            continue
        count = source.execute(
            "SELECT count(*) FROM opportunity_snapshots WHERE run_id = ? "
            "AND scope = ? AND COALESCE(workspace_id,'') = ''",
            (row["run_id"], scope),
        ).fetchone()[0]
        if count < 1:
            continue
        valid.append(row)
        if len(valid) == 2:
            break
    if len(valid) != 2:
        raise ValueError("not_enough_valid_legacy_runs")
    return list(reversed(valid))


def _insert_record(conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
                   row: dict[str, Any]) -> None:
    """表名/列名仅使用源码常量，不拼接任何外部输入。"""
    sql = ("INSERT INTO " + table + " (" + ",".join(columns) + ") VALUES ("
           + ",".join("?" for _ in columns) + ")")
    conn.execute(sql, [row[name] for name in columns])


def replay_history(source_db: str | Path, output_dir: str | Path,
                   *, scope: str = "AI Agents") -> dict[str, Any]:
    source_path = Path(source_db).expanduser().resolve(strict=True)
    out = Path(output_dir).expanduser().resolve()
    target_path = out / "replay.db"
    if source_path == target_path:
        raise ValueError("source_database_must_not_equal_output")
    # 原子性：输出文件已存在则拒绝覆盖，保护先前验收记录。
    if target_path.exists() or (out / "summary.json").exists():
        raise FileExistsError("replay_output_already_exists")
    if not scope.strip():
        raise ValueError("scope_is_required")

    # 输出路径只保存离线复制，绝不在原始生产库上创建工作区。
    out.mkdir(parents=True, exist_ok=True)
    before = _digest(source_path)
    src = sqlite3.connect("file:" + str(source_path) + "?mode=ro",
                          uri=True)
    src.execute("PRAGMA query_only=ON")
    src.row_factory = sqlite3.Row
    try:
        selected = _pick_runs(src, scope)
        with WorkspaceStore(target_path) as ws_store:
            workspace = ws_store.create(
                name="Radar V2 历史回放", scope=scope,
                language="en", country="US",
                objective="仅验证旧版真实扫描记录；非实时采集",
            )
        mapping: list[dict[str, Any]] = []
        with OpportunityReadStore(target_path) as v1_copy:
            for source_run in selected:
                original_id = source_run["run_id"]
                new_id = stable_id("offline-replay", workspace.workspace_id, original_id)
                record = dict(source_run)
                record["run_id"] = new_id
                record["workspace_id"] = workspace.workspace_id
                _insert_record(v1_copy.conn, "radar_runs", _RUN_COLUMNS, record)
                source_rows = src.execute(
                    "SELECT * FROM opportunity_snapshots WHERE run_id = ? "
                    "ORDER BY rank ASC, topic_id ASC",
                    (original_id,),
                ).fetchall()
                for snap in source_rows:
                    if snap["scope"] != scope or snap["workspace_id"] not in (None, ""):
                        raise ValueError("source_snapshot_identity_mismatch")
                    data = dict(snap)
                    data["snapshot_id"] = stable_id(
                        "offline-replay-snapshot", new_id, snap["snapshot_id"]
                    )
                    data["run_id"] = new_id
                    data["workspace_id"] = workspace.workspace_id
                    _insert_record(v1_copy.conn, "opportunity_snapshots",
                                   _SNAPSHOT_COLUMNS, data)
                mapping.append({
                    "source_run_id": original_id, "replay_run_id": new_id,
                    "generated_at": source_run["generated_at"],
                    "seed_source_mode": source_run["seed_source_mode"],
                    "source_snapshots": len(source_rows),
                })
            v1_copy.conn.commit()
        with TrackerStore(target_path) as track, HistoricalRunReader(target_path) as history:
            first, second = mapping
            schedule = track.create_schedule(
                workspace.workspace_id,
                next_due_at=first["generated_at"],
            )
            run1 = TrackerScheduler(
                track, ReplayScanAdapter(
                    history, scope=scope, curr_run_id=first["replay_run_id"]
                )
            ).tick(now=first["generated_at"])
            if len(run1) != 1 or run1[0]["status"] != "completed":
                raise RuntimeError("first_replay_failed")
            next_due = track.get_schedule(schedule["schedule_id"])["next_due_at"]
            tick_at = max(_utc(next_due), _utc(second["generated_at"])).isoformat()
            run2 = TrackerScheduler(
                track, ReplayScanAdapter(
                    history, scope=scope, prev_run_id=first["replay_run_id"],
                    curr_run_id=second["replay_run_id"],
                )
            ).tick(now=tick_at)
            if len(run2) != 1 or run2[0]["status"] != "completed":
                raise RuntimeError("second_replay_failed")
            diffs = track.list_diffs(tracker_run_id=run2[0]["tracker_run_id"])
            summary: dict[str, Any] = {
                "mode": "offline_historical_replay_not_new_collection",
                "provider_api_calls": 0,
                "source_database_sha256_before": before,
                "scope": scope,
                "workspace_id": workspace.workspace_id,
                "replay_database": str(target_path),
                "source_runs": mapping,
                "tracker_runs_completed": len(track.list_runs()),
                "diff_count": len(diffs),
                "change_counts": {
                    kind: sum(d["change_type"] == kind for d in diffs)
                    for kind in sorted({d["change_type"] for d in diffs})
                },
                "changes": [
                    {"topic_id": d["topic_id"], "topic_name": d["topic_name"],
                     "change_type": d["change_type"], "prev_rank": d["prev_rank"],
                     "curr_rank": d["curr_rank"], "magnitude": d["magnitude"]}
                    for d in diffs
                ],
            }
    finally:
        src.close()
    after = _digest(source_path)
    if after != before:
        raise RuntimeError("source_database_changed_during_replay")
    summary["source_database_sha256_after"] = after
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Radar V2 离线真实历史数据回放")
    parser.add_argument("--source", default=".radar/radar.db")
    parser.add_argument("--output", required=True,
                        help="全新的隔离输出目录；脚本不会覆盖已经存在的验收库")
    parser.add_argument("--scope", default="AI Agents")
    args = parser.parse_args()
    result = replay_history(args.source, args.output, scope=args.scope)
    print(json.dumps({
        "mode": result["mode"],
        "workspace_id": result["workspace_id"],
        "source_runs": result["source_runs"],
        "diff_count": result["diff_count"],
        "change_counts": result["change_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
