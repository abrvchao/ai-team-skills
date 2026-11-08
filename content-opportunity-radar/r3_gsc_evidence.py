"""R3 GSC 第一方证据校验：报告字符串不等于可信采集记录。

只读核对当前工作区、SQLite 采集运行和原始事件来源链；不会访问 Google API。
可防假 result.json，不能阻挡拥有本地 DB 写权限的人伪造 DB；后者需要 H3 签名。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from workspace import WorkspaceStore, workspace_collection_plan

FRESHNESS_LIMIT = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=5)
GSC_ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"


def _parse_time(value: object) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone missing")
    return dt.astimezone(timezone.utc)


def latest_verified_gsc_run(
    *, database: str, workspace_id: str,
    run_id: str | None = None,
    started_after: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """只接受最新的、有真实 OAuth 原始观测佐证的本工作区 GSC healthy 采集。"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        with WorkspaceStore(database, read_only=True, initialize=False) as store:
            workspace = store.get(workspace_id)
            if not workspace.enabled or not workspace.gsc_site_url:
                return None
            jobs = [job for job in workspace_collection_plan(workspace, database=database)["jobs"]
                    if job["provider"] == "gsc"]
            if len(jobs) != 1:
                return None
            job_id = jobs[0]["id"]
            row = store.conn.execute(
                """SELECT run_id, status, event_count, started_at, finished_at
                   FROM collection_runs WHERE job_id = ? AND provider = 'gsc'
                   ORDER BY finished_at DESC LIMIT 1""", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "healthy" or row["event_count"] < 1:
                return None
            if run_id is not None and row["run_id"] != run_id:
                return None
            began = _parse_time(row["started_at"])
            finished = _parse_time(row["finished_at"])
            if began > finished or finished > now + CLOCK_SKEW or now - finished > FRESHNESS_LIMIT:
                return None
            if started_after is not None and began < started_after.astimezone(timezone.utc):
                return None
            observations = store.conn.execute(
                """SELECT re.id, re.provenance_json
                   FROM event_observations eo JOIN raw_events re ON eo.event_id = re.id
                   WHERE eo.job_id = ? AND eo.provider = 'gsc'
                     AND eo.observed_at >= ? AND eo.observed_at <= ?
                     AND re.provider = 'gsc' AND re.source = ?
                     AND re.acquisition_method = 'oauth_api'""",
                (job_id, row["started_at"], (finished + CLOCK_SKEW).isoformat(),
                 workspace.gsc_site_url),
            ).fetchall()
            valid_ids: set[str] = set()
            for event in observations:
                provenance = json.loads(event["provenance_json"] or "{}")
                if (provenance.get("terms_class") == "first_party_oauth_top_rows"
                        and provenance.get("api_version") == "v3"
                        and provenance.get("endpoint") == GSC_ENDPOINT):
                    valid_ids.add(event["id"])
            if not valid_ids:
                return None
            return {
                "workspace_id": workspace_id,
                "run_id": row["run_id"],
                "status": row["status"],
                "event_count": int(row["event_count"]),
                "finished_at": finished.isoformat(),
                "property_sha256": hashlib.sha256(workspace.gsc_site_url.encode()).hexdigest(),
                "verified_events": len(valid_ids),
            }
    except (OSError, sqlite3.Error, KeyError, ValueError, TypeError, AttributeError):
        return None


def verify_gsc_report(
    report: Mapping[str, Any], *, database: str, now: datetime | None = None,
) -> bool:
    """R3 消费端重新核对 DB，不接受单靠 passed: true 的伪造文件。"""
    if not isinstance(report, Mapping) or report.get("passed") is not True:
        return False
    workspace_id = report.get("workspace_id")
    run_id = report.get("gsc_collection_run_id")
    if not isinstance(workspace_id, str) or not workspace_id or not isinstance(run_id, str) or not run_id:
        return False
    evidence = latest_verified_gsc_run(
        database=database, workspace_id=workspace_id, run_id=run_id, now=now
    )
    if evidence is None:
        return False
    return (
        report.get("gsc_provider_status") == evidence["status"]
        and type(report.get("gsc_event_count")) is int
        and report["gsc_event_count"] == evidence["event_count"]
        and report.get("gsc_property_sha256") == evidence["property_sha256"]
        and report.get("gsc_run_finished_at") == evidence["finished_at"]
        and report.get("provider_evidence_pass") is True
        and report.get("opportunity_output_pass") is True
        and type(report.get("top_opportunity_count")) is int
        and report["top_opportunity_count"] > 0
    )
