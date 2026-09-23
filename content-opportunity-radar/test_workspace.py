from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from api import create_handler
from collector import CollectionJob, CollectionService, CollectorConfig, RadarStore
from core import (
    AcquisitionMethod,
    CollectionResult,
    DataProvider,
    ProviderState,
    Provenance,
    RawEvent,
)
from read_model import OpportunityReadStore
from workspace import (
    WorkspaceStore,
    normalize_gsc_site,
    normalize_site_url,
    workspace_collection_plan,
)


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


class WorkspaceSeedProvider(DataProvider):
    id = "github"

    def __init__(self, event_id: str, title: str):
        self.event_id = event_id
        self.title = title

    def collect(self, request):
        event = RawEvent(
            id=self.event_id,
            provider="github",
            source="github.com",
            acquisition_method=AcquisitionMethod.OFFICIAL_API,
            retrieved_at=NOW,
            title=self.title,
            text=self.title,
            metrics={"stars": 10.0},
            provenance=Provenance("test"),
        )
        return CollectionResult(
            provider="github",
            status=ProviderState.HEALTHY,
            events=[event],
            collected_at=NOW,
        )


def opportunity_report(workspace_id: str | None, topic: str, evidence_id: str) -> dict:
    return {
        "mode": "discovery",
        "workspace_id": workspace_id,
        "scope": "AI",
        "seed": {"source_mode": "collector_store", "warnings": []},
        "candidate_count": 1,
        "scanned_count": 1,
        "scan_errors": [],
        "top_opportunities": [
            {
                "topic_id": f"topic:{topic.casefold().replace(' ', '-')}",
                "topic": topic,
                "rank_score": 80.0,
                "features": {"source_diversity": 50.0},
                "discovery": {"discovery_score": 70.0},
                "opportunity": {
                    "score": 78.0,
                    "components": {
                        "demand": 80.0,
                        "momentum": 75.0,
                        "supply_gap": 55.0,
                        "authority_fit": 50.0,
                        "business_fit": 60.0,
                        "freshness": 90.0,
                        "confidence": 82.0,
                    },
                    "reasons": ["Observed evidence."],
                    "evidence_ids": [evidence_id],
                },
                "events": [
                    {
                        "id": evidence_id,
                        "provider": "github",
                        "source": "github.com",
                        "acquisition_method": "official_api",
                        "url": f"https://github.com/example/{evidence_id}",
                        "title": topic,
                        "text": topic,
                        "retrieved_at": NOW.isoformat(),
                        "metrics": {"stars": 10},
                        "provenance": {"endpoint": "GET /search"},
                    }
                ],
            }
        ],
    }


class WorkspaceTests(unittest.TestCase):
    def test_public_domain_normalization_and_rejection(self):
        self.assertEqual(
            normalize_site_url("HTTPS://Example.COM:443/blog?q=x", required=True),
            "https://example.com",
        )
        self.assertEqual(
            normalize_site_url("http://Example.com:80/", required=True),
            "http://example.com",
        )
        self.assertEqual(
            normalize_gsc_site("sc-domain:Example.COM"),
            "sc-domain:example.com",
        )

        rejected = [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://localhost",
            "http://dev.local",
            "http://127.0.0.1",
            "http://10.0.0.1",
            "http://172.16.2.3",
            "http://192.168.1.10",
            "http://169.254.10.10",
            "http://user:pass@example.com",
            "http://intranet",
        ]
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_site_url(value, required=True)

        with self.assertRaises(ValueError):
            normalize_gsc_site("sc-domain:localhost")
        with self.assertRaises(ValueError):
            normalize_gsc_site("sc-domain:127.0.0.1")

    def test_workspace_persistence_competitor_dedupe_and_secret_free_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with WorkspaceStore(db) as store:
                workspace = store.create(
                    name="Example",
                    scope="AI Agents",
                    domain="https://Example.com/",
                    audience="engineering leaders",
                    objective="qualified leads",
                    competitors=[
                        "https://competitor.example",
                        "https://COMPETITOR.example/",
                    ],
                    gsc_site_url="sc-domain:example.com",
                    google_ads_customer_id="123-456-7890",
                )
                fetched = store.get(workspace.workspace_id)

            self.assertEqual(fetched.domain, "https://example.com")
            self.assertEqual(
                fetched.competitors,
                ["https://competitor.example"],
            )
            self.assertEqual(fetched.google_ads_customer_id, "1234567890")

            plan = workspace_collection_plan(fetched, database=str(db))
            blob = json.dumps(plan)
            self.assertIn("GSC_ACCESS_TOKEN", blob)
            self.assertIn("GOOGLE_ADS_ACCESS_TOKEN", blob)
            self.assertNotIn("access_token\": \"", blob)
            gsc = next(job for job in plan["jobs"] if job["provider"] == "gsc")
            self.assertEqual(
                gsc["metadata_env"]["access_token"],
                "GSC_ACCESS_TOKEN",
            )

    def test_stored_seed_events_are_isolated_by_workspace_job_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            jobs = [
                CollectionJob(
                    id="ws_a-github",
                    provider="a",
                    topic="AI",
                    interval_seconds=600,
                ),
                CollectionJob(
                    id="ws_b-github",
                    provider="b",
                    topic="AI",
                    interval_seconds=600,
                ),
            ]
            config = CollectorConfig(database=str(db), jobs=jobs)

            factories = {
                "a": lambda: WorkspaceSeedProvider("event-a", "Workspace A Signal"),
                "b": lambda: WorkspaceSeedProvider("event-b", "Workspace B Signal"),
            }
            # Factories return providers whose runtime provider id is github;
            # collection job identity is what isolates observations.
            service = CollectionService(
                config,
                provider_factories=factories,
                now_fn=lambda: NOW,
            )
            service.run_once(force=True)

            with RadarStore(db) as store:
                a = store.recent_events(
                    since=datetime(2026, 9, 23, tzinfo=timezone.utc),
                    providers=["github"],
                    job_prefix="ws_a-",
                )
                b = store.recent_events(
                    since=datetime(2026, 9, 23, tzinfo=timezone.utc),
                    providers=["github"],
                    job_prefix="ws_b-",
                )

            self.assertEqual([row.id for row in a], ["event-a"])
            self.assertEqual([row.id for row in b], ["event-b"])

    def test_read_model_keeps_same_scope_workspaces_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db) as store:
                store.record_discovery(
                    opportunity_report("ws_a", "Agent Memory", "e-a"),
                    generated_at=NOW,
                )
                store.record_discovery(
                    opportunity_report("ws_b", "Context Engineering", "e-b"),
                    generated_at=NOW,
                )
                store.record_discovery(
                    opportunity_report(None, "Legacy Topic", "e-legacy"),
                    generated_at=NOW,
                )

                a = store.list_opportunities(
                    scope="AI",
                    workspace_id="ws_a",
                )
                b = store.list_opportunities(
                    scope="AI",
                    workspace_id="ws_b",
                )
                legacy = store.list_opportunities(scope="AI")

            self.assertEqual([row["topic"] for row in a["items"]], ["Agent Memory"])
            self.assertEqual([row["topic"] for row in b["items"]], ["Context Engineering"])
            self.assertEqual([row["topic"] for row in legacy["items"]], ["Legacy Topic"])

    def test_old_read_model_schema_migrates_workspace_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE radar_runs (
                    run_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    seed_source_mode TEXT NOT NULL,
                    candidate_count INTEGER NOT NULL,
                    scanned_count INTEGER NOT NULL,
                    warning_json TEXT NOT NULL,
                    error_json TEXT NOT NULL
                );
                CREATE TABLE opportunity_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    discovery_score REAL NOT NULL,
                    opportunity_score REAL NOT NULL,
                    rank_score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    freshness REAL NOT NULL,
                    components_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    discovery_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    research_pack_json TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
            conn.close()

            with OpportunityReadStore(db) as store:
                run_cols = {
                    row["name"]
                    for row in store.conn.execute("PRAGMA table_info(radar_runs)")
                }
                opp_cols = {
                    row["name"]
                    for row in store.conn.execute("PRAGMA table_info(opportunity_snapshots)")
                }
            self.assertIn("workspace_id", run_cols)
            self.assertIn("workspace_id", opp_cols)

    def test_workspace_read_api_lists_and_scopes_opportunities(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with WorkspaceStore(db) as ws_store:
                a = ws_store.create(name="A", scope="AI", domain="https://a.example")
                b = ws_store.create(name="B", scope="AI", domain="https://b.example")
            with OpportunityReadStore(db) as store:
                store.record_discovery(
                    opportunity_report(a.workspace_id, "A Topic", "e-a"),
                    generated_at=NOW,
                )
                store.record_discovery(
                    opportunity_report(b.workspace_id, "B Topic", "e-b"),
                    generated_at=NOW,
                )

            server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(str(db)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1",
                    server.server_address[1],
                    timeout=5,
                )
                conn.request("GET", "/v1/workspaces")
                response = conn.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    {item["workspace_id"] for item in payload["items"]},
                    {a.workspace_id, b.workspace_id},
                )

                conn.request(
                    "GET",
                    f"/v1/workspaces/{a.workspace_id}/opportunities",
                )
                response = conn.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    [item["topic"] for item in payload["items"]],
                    ["A Topic"],
                )
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
