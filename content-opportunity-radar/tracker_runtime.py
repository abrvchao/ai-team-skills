"""Radar V2 production runtime: safe workspace scans, scheduling and persistence."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from core import isoformat, utcnow
from radar import run_discovery
from read_model import OpportunityReadStore
from tracker import DEFAULT_CRON, TrackerStore
from tracker_scheduler import ScanResult, TrackerScheduler, next_cron_due
from workspace import WorkspaceStore


LOGGER = logging.getLogger(__name__)


class RadarTrackerRuntime:
    """将 V1 的免费公开源扫描接入 V2；提醒只落库，不向外发送。"""

    def __init__(
        self,
        database: str | Path,
        *,
        scan: Callable[[dict], ScanResult] | None = None,
        poll_seconds: int = 30,
        top_n: int = 5,
    ) -> None:
        if poll_seconds < 5:
            raise ValueError("poll_seconds must be at least 5")
        if top_n < 1 or top_n > 5:
            raise ValueError("top_n must be between 1 and 5")
        self.database = str(Path(database).expanduser().resolve())
        self.scan = scan or self._scan_workspace
        self.poll_seconds = poll_seconds
        self.top_n = top_n
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._init_lock = threading.Lock()
        self._initialized = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def initialize(self) -> dict:
        """幂等创建 V2 表，并为启用工作区建立默认关闭的 6 小时计划。"""
        with self._init_lock:
            with TrackerStore(self.database):
                pass
            now = isoformat(utcnow())
            with WorkspaceStore(
                self.database, initialize=False, read_only=True
            ) as workspaces:
                enabled = workspaces.list(include_disabled=False)
            with TrackerStore(self.database) as store:
                for workspace in enabled:
                    existing = store.list_schedules(
                        workspace_id=workspace.workspace_id
                    )
                    if not existing:
                        store.create_schedule(
                            workspace.workspace_id,
                            cron_expr=DEFAULT_CRON,
                            enabled=False,
                            next_due_at=next_cron_due(DEFAULT_CRON, after=now),
                        )
            self._initialized = True
        return self.health()

    def health(self) -> dict:
        expected = {
            "tracker_schedules",
            "tracker_runs",
            "tracker_diffs",
            "tracker_alerts",
        }
        try:
            with TrackerStore(
                self.database, initialize=False, read_only=True
            ) as store:
                present = set(store.table_names())
                missing = sorted(expected - present)
                if missing:
                    return {
                        "status": "not_initialized",
                        "missing_tables": missing,
                        "scheduler_running": self.running,
                    }
                counts = {
                    table: int(
                        store.conn.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0]
                    )
                    for table in sorted(expected)
                }
            return {
                "status": "ok",
                "schema": "radar-v2",
                "scheduler_running": self.running,
                "counts": counts,
            }
        except Exception:
            return {
                "status": "unavailable",
                "scheduler_running": self.running,
            }

    def create_schedule(
        self,
        workspace_id: str,
        *,
        cron_expr: str = DEFAULT_CRON,
    ) -> dict:
        self.initialize()
        with WorkspaceStore(
            self.database, initialize=False, read_only=True
        ) as workspaces:
            workspace = workspaces.get(workspace_id)
        if not workspace.enabled:
            raise ValueError("workspace_disabled")
        now = isoformat(utcnow())
        due = next_cron_due(cron_expr, after=now)
        with TrackerStore(self.database) as store:
            schedule = store.create_schedule(
                workspace_id,
                cron_expr=cron_expr,
                next_due_at=due,
            )
            if not schedule["enabled"]:
                schedule = store.set_schedule_enabled(
                    schedule["schedule_id"], True
                )
        return schedule

    def run_now(self, workspace_id: str) -> dict:
        """人工触发一次工作区扫描；仍经过原子 claim、完整性校验和失败清理。"""
        self.initialize()
        with WorkspaceStore(
            self.database, initialize=False, read_only=True
        ) as workspaces:
            workspace = workspaces.get(workspace_id)
        if not workspace.enabled:
            raise ValueError("workspace_disabled")

        now = isoformat(utcnow())
        with TrackerStore(self.database) as store:
            schedules = store.list_schedules(workspace_id=workspace_id)
            if not schedules:
                schedule = store.create_schedule(
                    workspace_id,
                    cron_expr=DEFAULT_CRON,
                    next_due_at=now,
                )
            else:
                schedule = next(
                    (item for item in schedules if item["enabled"]),
                    schedules[0],
                )
                if not schedule["enabled"]:
                    schedule = store.set_schedule_enabled(
                        schedule["schedule_id"], True
                    )
                store.request_schedule_now(schedule["schedule_id"], now=now)
            results = TrackerScheduler(
                store, self.scan, top_n=self.top_n
            ).tick(now=now)
        completed = any(item.get("status") == "completed" for item in results)
        return {
            "workspace_id": workspace_id,
            "status": "completed" if completed else "failed",
            "items": results,
        }

    def tick(self, *, now=None) -> list[dict]:
        self.initialize()
        with TrackerStore(self.database) as store:
            return TrackerScheduler(
                store, self.scan, top_n=self.top_n
            ).tick(now=now or utcnow())

    def start_background_scheduler(self) -> None:
        self.initialize()
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="radar-v2-tracker",
            daemon=True,
        )
        self._thread.start()

    def stop_background_scheduler(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(1, self.poll_seconds + 1))
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with TrackerStore(self.database) as store:
                    TrackerScheduler(
                        store, self.scan, top_n=self.top_n
                    ).tick(now=utcnow())
            except Exception:
                LOGGER.exception("Radar V2 tracker scheduler tick failed")
            self._stop.wait(self.poll_seconds)

    @staticmethod
    def _usable_run(run, workspace_id: str, scope: str) -> bool:
        return bool(
            run
            and run.workspace_id == workspace_id
            and run.scope == scope
            and run.candidate_count > 0
            and run.scanned_count > 0
            and not run.scan_errors
        )

    def _scan_workspace(self, schedule: dict) -> ScanResult:
        workspace_id = str(schedule.get("workspace_id") or "")
        if not workspace_id:
            raise ValueError("workspace_id_required")
        with WorkspaceStore(
            self.database, initialize=False, read_only=True
        ) as workspaces:
            workspace = workspaces.get(workspace_id)
        if not workspace.enabled:
            raise ValueError("workspace_disabled")

        requested_previous = schedule.get("previous_radar_run_id")
        previous_run = None
        previous_items = None
        with OpportunityReadStore(
            self.database, initialize=False, read_only=True
        ) as read_store:
            if requested_previous:
                previous_run = read_store.run_by_id(str(requested_previous))
                if not self._usable_run(
                    previous_run, workspace_id, workspace.scope
                ):
                    raise ValueError("previous_snapshot_invalid")
            else:
                candidate = read_store.latest_run(
                    workspace.scope, workspace_id=workspace_id
                )
                if self._usable_run(candidate, workspace_id, workspace.scope):
                    previous_run = candidate
            if previous_run:
                previous_page = read_store.list_opportunities(
                    scope=workspace.scope,
                    workspace_id=workspace_id,
                    run_id=previous_run.run_id,
                    limit=100,
                )
                previous_items = previous_page.get("items") or []
                if not previous_items:
                    if requested_previous:
                        raise ValueError("previous_snapshot_invalid")
                    previous_run = None
                    previous_items = None

        # 只使用 GitHub / Hacker News / Google News / GDELT 免费公开源。
        # 明确不传 GSC、Google Ads、网站抓取或付费翻译凭据。
        base = Path(self.database).parent
        output = run_discovery(
            scope=workspace.scope,
            seed_limit=12,
            candidate_count=8,
            top_n=self.top_n,
            deep_limit=5,
            snapshot_path=str(base / "snapshots.jsonl"),
            cache_path=str(base / "provider-cache.json"),
            content_snapshot_path=str(base / "page-snapshots.jsonl"),
            website=None,
            gsc_site_url=None,
            gsc_access_token=None,
            google_ads_customer_id=None,
            google_ads_access_token=None,
            hydrate_content=False,
            read_model_db=self.database,
            workspace_id=workspace_id,
            language=workspace.language,
            country=workspace.country,
            include_research_pack=True,
            seed_mode="native",
        )
        read_model = output.get("read_model") or {}
        radar_run_id = str(read_model.get("run_id") or "")
        if not radar_run_id:
            raise ValueError("scan_run_missing")
        with OpportunityReadStore(
            self.database, initialize=False, read_only=True
        ) as read_store:
            current_run = read_store.run_by_id(radar_run_id)
            if not self._usable_run(
                current_run, workspace_id, workspace.scope
            ):
                raise ValueError("scan_incomplete")
            current_page = read_store.list_opportunities(
                scope=workspace.scope,
                workspace_id=workspace_id,
                run_id=radar_run_id,
                limit=100,
            )
        current_items = current_page.get("items") or []
        seed = output.get("seed") or {}
        complete = bool(
            seed.get("event_count", 0) > 0
            and output.get("candidate_count", 0) > 0
            and output.get("scanned_count", 0) > 0
            and not output.get("scan_errors")
            and current_items
        )
        return ScanResult(
            radar_run_id=radar_run_id,
            current_items=current_items,
            previous_items=previous_items,
            complete=complete,
            previous_radar_run_id=(
                previous_run.run_id if previous_run else None
            ),
        )
