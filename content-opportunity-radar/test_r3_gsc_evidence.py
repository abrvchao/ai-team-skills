"""R3 GSC 报告真实性负向测试：本文件只用临时 SQLite 与合成事件，不触发网络。"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector import CollectionJob, RadarStore
from core import (
    AcquisitionMethod, CollectionResult, ProviderState, Provenance, RawEvent,
)
from gsc import GSC_ENDPOINT
from r3_gsc_evidence import latest_verified_gsc_run, verify_gsc_report
from r3_gsc_validation import validate_result
from r3_readiness import build_readiness
from workspace import WorkspaceStore


class GSCFirstPartyEvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gsc-evidence-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "radar.db")
        self.root = Path(self.temp.name) / "r3"
        (self.root / "gsc").mkdir(parents=True)
        with WorkspaceStore(self.database) as store:
            w = store.create(
                name="Synthetic Workspace", scope="AI",
                domain="https://example.com",
                gsc_site_url="sc-domain:example.com",
            )
        self.workspace_id = w.workspace_id
        self.now = datetime.now(timezone.utc)
        self.began = self.now - timedelta(minutes=4)
        self.finished = self.now - timedelta(minutes=3)
        self.payload = {
            "workspace": {"workspace_id": self.workspace_id},
            "collection": {"jobs": [{
                "provider": "gsc", "job_id": f"{self.workspace_id}-gsc",
                "status": "healthy", "event_count": 1,
            }]},
            "top_opportunities": [{"rank": 1, "topic": "SYNTHETIC_ONLY"}],
        }

    def record(self, *, source="sc-domain:example.com",
               method=AcquisitionMethod.OAUTH_API,
               provider="gsc", terms="first_party_oauth_top_rows",
               endpoint=GSC_ENDPOINT, status=ProviderState.HEALTHY,
               started_at=None, finished_at=None):
        started_at = started_at or self.began
        finished_at = finished_at or self.finished
        event = RawEvent(
            id="synthetic-gsc-1", provider=provider, source=source,
            acquisition_method=method, retrieved_at=finished_at,
            title="synthetic keyword", text="synthetic keyword",
            provenance=Provenance(terms_class=terms, api_version="v3",
                                  endpoint=endpoint),
            metrics={"clicks": 1.0},
        )
        job = CollectionJob(id=f"{self.workspace_id}-gsc", provider="gsc",
                            topic="AI", interval_seconds=21600)
        with RadarStore(self.database) as store:
            store.record_result(
                job,
                CollectionResult(provider="gsc", status=status,
                                 events=[event], collected_at=finished_at),
                started_at=started_at, finished_at=finished_at,
                next_due_at=finished_at + timedelta(hours=6),
                consecutive_failures=0,
            )

    def validate(self, payload=None, **kwargs):
        return validate_result(
            self.payload if payload is None else payload,
            workspace_id=self.workspace_id, database=self.database,
            started_after=self.began - timedelta(seconds=1),
            now=self.now, **kwargs,
        )

    def report_readiness(self, report):
        (self.root / "gsc" / "result.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        gates = build_readiness(
            database=self.database, r3_root=self.root,
            environ={"GSC_ACCESS_TOKEN": "synthetic-token-not-real"},
        )["gates"]
        return next(g for g in gates if g["id"] == "gsc_first_party")

    def test_original_forged_dictionary_fails_closed_without_database_context(self):
        fake = {
            "collection": {"jobs": [{
                "provider": "gsc", "status": "healthy", "event_count": 1,
            }]},
            "top_opportunities": [{"topic": "SYNTHETIC_NOT_REAL"}],
            "workspace": {"workspace_id": "SYNTHETIC_NOT_REAL"},
        }
        result = validate_result(fake)
        self.assertFalse(result["passed"])
        self.assertFalse(result["provider_evidence_pass"])
        self.assertIsNone(result["gsc_collection_run_id"])

    def test_fake_payload_with_valid_workspace_and_token_but_no_db_run_fails(self):
        result = self.validate()
        self.assertFalse(result["passed"])
        self.assertFalse(self.report_readiness({"passed": True})["passed"])

    def test_positive_synthetic_database_fixture_demonstrates_mechanical_gate(self):
        # 注意：这只是本地 DB 门禁测试，合成记录绝不能用于声称真实 Google API 验收。
        self.record()
        result = self.validate()
        self.assertTrue(result["passed"])
        self.assertTrue(result["source_verified"])
        self.assertTrue(result["gsc_collection_run_id"])
        self.assertTrue(verify_gsc_report(result, database=self.database, now=self.now))
        self.assertTrue(self.report_readiness(result)["passed"])

    def test_report_forgery_cannot_bypass_unverified_sqlite_evidence(self):
        self.record(source="malicious.example.invalid")
        result = self.validate()
        self.assertFalse(result["passed"])
        forged = {
            "passed": True, "workspace_id": self.workspace_id,
            "gsc_collection_run_id": "arbitrary-string",
            "gsc_provider_status": "healthy", "gsc_event_count": 1,
            "gsc_property_sha256": "fake",
            "gsc_run_finished_at": self.finished.isoformat(),
            "provider_evidence_pass": True, "opportunity_output_pass": True,
            "top_opportunity_count": 1,
        }
        self.assertFalse(self.report_readiness(forged)["passed"])

    def test_missing_oauth_method_or_wrong_provenance_never_pass(self):
        for changes in (
            {"method": AcquisitionMethod.HTML},
            {"terms": "synthetic"},
            {"endpoint": "https://example.com/not-google"},
            {"provider": "other"},
        ):
            with self.subTest(changes=changes):
                with RadarStore(self.database) as store:
                    # isolate scenario by deleting previous synthetic observations
                    store.conn.execute("DELETE FROM event_observations")
                    store.conn.execute("DELETE FROM raw_events")
                    store.conn.execute("DELETE FROM collection_runs")
                    store.conn.commit()
                self.record(**changes)
                self.assertFalse(self.validate()["passed"])

    def test_wrong_workspace_and_wrong_job_identifiers_fail(self):
        self.record()
        fake = json.loads(json.dumps(self.payload))
        fake["workspace"]["workspace_id"] = "other-workspace"
        self.assertFalse(self.validate(fake)["passed"])
        fake = json.loads(json.dumps(self.payload))
        fake["collection"]["jobs"][0]["job_id"] = "other-workspace-gsc"
        self.assertFalse(self.validate(fake)["passed"])

    def test_wrong_event_count_degraded_status_and_stale_run_fail(self):
        self.record()
        fake = json.loads(json.dumps(self.payload))
        fake["collection"]["jobs"][0]["event_count"] = 2000
        self.assertFalse(self.validate(fake)["passed"])
        fake = json.loads(json.dumps(self.payload))
        fake["collection"]["jobs"][0]["status"] = "degraded"
        self.assertFalse(self.validate(fake)["passed"])
        self.assertFalse(latest_verified_gsc_run(
            database=self.database, workspace_id=self.workspace_id,
            started_after=self.now - timedelta(minutes=1),
            now=self.now,
        ))
        self.assertFalse(latest_verified_gsc_run(
            database=self.database, workspace_id=self.workspace_id,
            now=self.now + timedelta(hours=25),
        ))

    def test_latest_failed_run_invalidates_previous_healthy_run(self):
        self.record()
        self.record(
            status=ProviderState.FAILED, started_at=self.finished + timedelta(seconds=1),
            finished_at=self.finished + timedelta(seconds=2),
        )
        self.assertFalse(self.validate()["passed"])

    def test_modified_or_stale_report_cannot_trick_readiness_consumer(self):
        self.record()
        original = self.validate()
        for key, value in (
            ("gsc_collection_run_id", "fake"),
            ("gsc_event_count", 999),
            ("gsc_provider_status", "degraded"),
            ("gsc_property_sha256", "forged"),
            ("gsc_run_finished_at", "2021-01-01T00:00:00+00:00"),
            ("workspace_id", "different-workspace"),
        ):
            with self.subTest(key=key):
                candidate = {**original, key: value, "passed": True}
                self.assertFalse(self.report_readiness(candidate)["passed"])
        # 修改数据库中的工作区目标站点后，旧报告失效。
        with WorkspaceStore(self.database) as store:
            store.conn.execute(
                "UPDATE workspaces SET gsc_site_url = ? WHERE workspace_id = ?",
                ("sc-domain:changed.example.com", self.workspace_id),
            )
            store.conn.commit()
        self.assertFalse(self.report_readiness(original)["passed"])

    def test_even_valid_db_evidence_requires_current_token_at_readiness(self):
        self.record()
        r = self.validate()
        (self.root / "gsc" / "result.json").write_text(json.dumps(r), encoding="utf8")
        gates = build_readiness(database=self.database, r3_root=self.root, environ={})["gates"]
        self.assertFalse(next(g for g in gates if g["id"] == "gsc_first_party")["passed"])


if __name__ == "__main__":
    unittest.main()
