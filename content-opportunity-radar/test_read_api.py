from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from api import create_handler
from read_model import OpportunityReadStore


T0 = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def evidence(event_id: str, provider: str = "github") -> dict:
    return {
        "id": event_id,
        "provider": provider,
        "source": "example.test",
        "acquisition_method": "official_api",
        "url": f"https://example.test/{event_id}",
        "title": f"Evidence {event_id}",
        "text": "Observed source evidence",
        "author": "source-author",
        "community": "source-community",
        "published_at": "2026-09-23T09:00:00+00:00",
        "retrieved_at": "2026-09-23T10:00:00+00:00",
        "language": "en",
        "country": "US",
        "metrics": {"stars": 42},
        "provenance": {
            "endpoint": "https://api.example.test/search?access_token=endpoint-secret",
            "api_version": "v1",
            "access_token": "must-not-leak",
        },
    }


def opportunity_row(
    topic_id: str,
    topic: str,
    score: float,
    evidence_id: str,
    *,
    rank_score: float | None = None,
) -> dict:
    return {
        "topic_id": topic_id,
        "topic": topic,
        "rank_score": score if rank_score is None else rank_score,
        "features": {
            "source_diversity": 75.0,
            "cross_source_confirmation": 70.0,
        },
        "discovery": {
            "discovery_score": score - 5,
            "provider_count": 3,
        },
        "opportunity": {
            "score": score,
            "components": {
                "demand": 80.0,
                "momentum": 75.0,
                "supply_gap": 60.0,
                "authority_fit": 55.0,
                "business_fit": 65.0,
                "freshness": 90.0,
                "confidence": 82.0,
            },
            "reasons": ["Cross-source evidence supports the opportunity."],
            "evidence_ids": [evidence_id],
        },
        "events": [evidence(evidence_id)],
        "research_pack": {
            "topic": topic,
            "citations": [{"evidence_id": evidence_id}],
            "guardrails": ["Use cited evidence only."],
        },
    }


def report(rows: list[dict], *, scope: str = "AI") -> dict:
    return {
        "mode": "discovery",
        "scope": scope,
        "seed": {
            "source_mode": "collector_store",
            "warnings": [],
        },
        "candidate_count": len(rows) + 2,
        "scanned_count": len(rows),
        "scan_errors": [],
        "top_opportunities": rows,
    }


class ReadModelTests(unittest.TestCase):
    def test_append_only_history_and_stable_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db) as store:
                store.record_discovery(
                    report([
                        opportunity_row("topic:a", "Agent Memory", 88, "e1"),
                        opportunity_row("topic:b", "Context Engineering", 82, "e2"),
                    ]),
                    generated_at=T0,
                )
                store.record_discovery(
                    report([
                        opportunity_row("topic:b", "Context Engineering", 91, "e2"),
                        opportunity_row("topic:a", "Agent Memory", 86, "e1"),
                    ]),
                    generated_at=T0 + timedelta(hours=1),
                )

                first = store.list_opportunities(scope="AI", limit=1)
                self.assertEqual(first["items"][0]["topic_id"], "topic:b")
                self.assertIsNotNone(first["next_cursor"])

                second = store.list_opportunities(
                    scope="AI",
                    limit=1,
                    cursor=first["next_cursor"],
                )
                self.assertEqual(second["items"][0]["topic_id"], "topic:a")
                self.assertIsNone(second["next_cursor"])

                history = store.opportunity_history("topic:a", scope="AI")
                self.assertEqual(len(history), 2)
                self.assertEqual(history[0]["rank"], 2)
                self.assertEqual(history[1]["rank"], 1)
                self.assertEqual(store.health()["radar_runs"], 2)

    def test_evidence_is_traceable_and_sensitive_provenance_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db) as store:
                store.record_discovery(
                    report([
                        opportunity_row("topic:a", "Agent Memory", 88, "e1"),
                    ]),
                    generated_at=T0,
                )

                item = store.get_opportunity("topic:a", scope="AI")
                self.assertEqual(item["evidence_ids"], ["e1"])
                self.assertEqual(item["source_summary"][0]["provider"], "github")

                row = store.get_evidence("e1")
                self.assertIn("[REDACTED]", row["provenance"]["endpoint"])
                self.assertEqual(row["provenance"]["access_token"], "[REDACTED]")
                blob = db.read_bytes().decode("utf-8", errors="ignore")
                self.assertNotIn("must-not-leak", blob)
                self.assertNotIn("endpoint-secret", blob)
                self.assertIsNone(store.get_evidence("missing"))

                pack = store.research_pack("topic:a", scope="AI")
                self.assertEqual(pack["citations"][0]["evidence_id"], "e1")

    def test_provider_health_is_safe_and_marks_degraded_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db) as store:
                store.conn.executescript(
                    """
                    CREATE TABLE job_state (
                        job_id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        last_started_at TEXT,
                        last_finished_at TEXT,
                        last_status TEXT,
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        next_due_at TEXT,
                        rate_limit_reset_at TEXT,
                        last_event_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE collection_runs (
                        run_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        event_count INTEGER NOT NULL,
                        warning_json TEXT NOT NULL,
                        rate_remaining INTEGER,
                        rate_reset_at TEXT
                    );
                    """
                )
                store.conn.execute(
                    """
                    INSERT INTO job_state (
                        job_id, provider, last_started_at, last_finished_at,
                        last_status, consecutive_failures, next_due_at,
                        rate_limit_reset_at, last_event_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "github-ai",
                        "github",
                        "2026-09-23T09:00:00+00:00",
                        "2026-09-23T09:00:05+00:00",
                        "degraded",
                        0,
                        "2026-09-23T09:30:00+00:00",
                        None,
                        3,
                        "2026-09-23T09:00:05+00:00",
                    ),
                )
                store.conn.execute(
                    """
                    INSERT INTO collection_runs (
                        run_id, job_id, provider, started_at, finished_at,
                        status, event_count, warning_json,
                        rate_remaining, rate_reset_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "r1",
                        "github-ai",
                        "github",
                        "2026-09-23T09:00:00+00:00",
                        "2026-09-23T09:00:05+00:00",
                        "degraded",
                        3,
                        json.dumps([
                            "Authorization: Bearer top-secret",
                            "access_token=abc123",
                        ]),
                        4,
                        None,
                    ),
                )
                store.conn.commit()

                health = store.provider_health()[0]
                self.assertTrue(health["degraded"])
                self.assertEqual(health["event_count"], 3)
                serialized = json.dumps(health)
                self.assertNotIn("top-secret", serialized)
                self.assertNotIn("abc123", serialized)
                self.assertIn("[REDACTED]", serialized)


class APITests(unittest.TestCase):
    def test_read_only_api_serves_opportunities_evidence_and_rejects_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db) as store:
                store.record_discovery(
                    report([
                        opportunity_row("topic:a", "Agent Memory", 88, "e1"),
                        opportunity_row("topic:b", "Context Engineering", 82, "e2"),
                    ]),
                    generated_at=T0,
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

                conn.request("GET", "/v1/opportunities?scope=AI&limit=1")
                response = conn.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["items"][0]["topic_id"], "topic:a")
                self.assertIsNotNone(payload["next_cursor"])

                conn.request("GET", "/v1/evidence/e1")
                response = conn.getresponse()
                evidence_payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(evidence_payload["provider"], "github")

                conn.request("GET", "/v1/opportunities/topic%3Aa/research-pack")
                response = conn.getresponse()
                pack = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(pack["citations"][0]["evidence_id"], "e1")

                conn.request("POST", "/v1/opportunities", body=b"{}")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 405)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
