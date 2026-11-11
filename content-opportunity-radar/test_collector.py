from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector import (
    CollectionJob,
    CollectionService,
    CollectorConfig,
    RadarStore,
)
from core import (
    AcquisitionMethod,
    CollectionRequest,
    CollectionResult,
    DataProvider,
    ProviderState,
    Provenance,
    RateLimit,
    RawEvent,
)


START = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value=START):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def make_event(metric=10.0):
    return RawEvent(
        id="event-1",
        provider="fake",
        source="example.test",
        acquisition_method=AcquisitionMethod.OFFICIAL_API,
        retrieved_at=START,
        external_id="external-1",
        url="https://example.test/1",
        title="Agent memory signal",
        text="Observed signal",
        metrics={"score": metric},
        raw={"kind": "test"},
        provenance=Provenance(
            terms_class="test",
            endpoint="GET /events",
        ),
    )


class StaticProvider(DataProvider):
    id = "fake"

    def __init__(self, metric=10.0):
        self.metric = metric

    def collect(self, request):
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=[make_event(self.metric)],
        )


class StoredGitHubProvider(DataProvider):
    id = "github"

    def collect(self, request):
        item = make_event(12.0)
        item.id = "stored-github-1"
        item.provider = self.id
        item.source = "github.com"
        item.title = "Context engineering patterns for AI agents"
        item.raw = {"kind": "repository", "topics": ["context-engineering"]}
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=[item],
        )


class FailingProvider(DataProvider):
    id = "failing"
    retry_attempts = 1

    def collect(self, request):
        raise RuntimeError("boom")


class RateLimitedProvider(DataProvider):
    id = "rate"

    def __init__(self, reset_at):
        self.reset_at = reset_at

    def collect(self, request):
        return CollectionResult(
            provider=self.id,
            status=ProviderState.RATE_LIMITED,
            rate_limit=RateLimit(
                remaining=0,
                reset_at=self.reset_at,
            ),
            warnings=["rate limited"],
        )


class AuthCaptureProvider(DataProvider):
    id = "auth"

    def __init__(self, captured):
        self.captured = captured

    def collect(self, request):
        self.captured.update(request.metadata)
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=[],
        )


class LeakyProvider(DataProvider):
    id = "leaky"

    def collect(self, request):
        leaked = RawEvent(
            id="leaky-1",
            provider=self.id,
            source="example.test",
            acquisition_method=AcquisitionMethod.OFFICIAL_API,
            retrieved_at=START,
            title="Safe title",
            metrics={"count": 1.0},
            raw={
                "headers": {
                    "Authorization": "Bearer raw-secret",
                    "X-Safe": "safe-value",
                },
                "cookie": "session=raw-cookie-secret",
                "nested": {
                    "client_secret": "raw-client-secret",
                    "note": "keep-me",
                },
            },
            provenance=Provenance(
                terms_class="test",
                endpoint="GET /leaky",
            ),
        )
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=[leaked],
        )


def config(db_path, jobs):
    return CollectorConfig(
        database=str(db_path),
        jobs=jobs,
    )


def job(job_id="job-1", provider="fake", interval=600, **kwargs):
    item = CollectionJob(
        id=job_id,
        provider=provider,
        topic=kwargs.pop("topic", "AI"),
        interval_seconds=interval,
        limit=kwargs.pop("limit", 20),
        metadata=kwargs.pop("metadata", {}),
        metadata_env=kwargs.pop("metadata_env", {}),
        enabled=kwargs.pop("enabled", True),
        max_backoff_seconds=kwargs.pop("max_backoff_seconds", 21600),
    )
    item.validate()
    return item


class CollectorTests(unittest.TestCase):
    def test_config_rejects_literal_secret(self):
        with self.assertRaises(ValueError):
            CollectorConfig.from_dict({
                "jobs": [{
                    "id": "bad",
                    "provider": "gsc",
                    "topic": "site",
                    "interval_seconds": 3600,
                    "metadata": {"access_token": "do-not-store-me"},
                }]
            })

    def test_config_rejects_nested_and_token_shaped_literal_secrets(self):
        cases = [
            {"github_token": "do-not-store-me"},
            {"headers": {"Authorization": "Bearer do-not-store-me"}},
            {"auth": {"refresh_token": "do-not-store-me"}},
            {"cookies": [{"session_cookie": "do-not-store-me"}]},
            {"credentials": {"private_key": "do-not-store-me"}},
        ]
        for metadata in cases:
            with self.subTest(metadata=metadata):
                with self.assertRaises(ValueError):
                    CollectorConfig.from_dict({
                        "jobs": [{
                            "id": "bad-nested",
                            "provider": "gsc",
                            "topic": "site",
                            "interval_seconds": 3600,
                            "metadata": metadata,
                        }]
                    })

    def test_first_run_executes_and_second_waits_until_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            clock = Clock()
            service = CollectionService(
                config(db, [job()]),
                provider_factories={"fake": StaticProvider},
                now_fn=clock,
            )
            first = service.run_once()
            self.assertTrue(first["jobs"][0]["executed"])
            self.assertEqual(first["jobs"][0]["status"], "healthy")

            second = service.run_once()
            self.assertFalse(second["jobs"][0]["executed"])
            self.assertEqual(second["jobs"][0]["reason"], "not_due")

            clock.advance(601)
            third = service.run_once()
            self.assertTrue(third["jobs"][0]["executed"])

    def test_raw_event_dedupes_but_observations_and_metrics_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            clock = Clock()
            metrics = [10.0, 20.0]

            def factory():
                return StaticProvider(metrics.pop(0))

            service = CollectionService(
                config(db, [job()]),
                provider_factories={"fake": factory},
                now_fn=clock,
            )
            service.run_once(force=True)
            clock.advance(600)
            service.run_once(force=True)

            conn = sqlite3.connect(db)
            raw_count = conn.execute(
                "SELECT COUNT(*) FROM raw_events"
            ).fetchone()[0]
            observation_count = conn.execute(
                "SELECT COUNT(*) FROM event_observations"
            ).fetchone()[0]
            retrieval_count, metrics_json = conn.execute(
                "SELECT retrieval_count, metrics_json FROM raw_events WHERE id='event-1'"
            ).fetchone()
            event_metric_count = conn.execute(
                """
                SELECT COUNT(*) FROM metric_snapshots
                WHERE subject_type='event' AND subject_id='event-1' AND metric='score'
                """
            ).fetchone()[0]
            conn.close()

            self.assertEqual(raw_count, 1)
            self.assertEqual(observation_count, 2)
            self.assertEqual(retrieval_count, 2)
            self.assertIn("20.0", metrics_json)
            self.assertEqual(event_metric_count, 2)

    def test_one_failed_provider_does_not_block_other_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            service = CollectionService(
                config(db, [
                    job("bad", "failing"),
                    job("good", "fake"),
                ]),
                provider_factories={
                    "failing": FailingProvider,
                    "fake": StaticProvider,
                },
                now_fn=Clock(),
            )
            result = service.run_once(force=True)
            statuses = {
                row["job_id"]: row.get("status")
                for row in result["jobs"]
            }
            self.assertEqual(statuses["bad"], "failed")
            self.assertEqual(statuses["good"], "healthy")
            self.assertEqual(result["counts"]["collection_runs"], 2)

    def test_rate_limit_reset_controls_next_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            clock = Clock()
            reset = START + timedelta(hours=2)
            service = CollectionService(
                config(db, [job("limited", "rate", interval=600)]),
                provider_factories={
                    "rate": lambda: RateLimitedProvider(reset),
                },
                now_fn=clock,
            )
            first = service.run_once(force=True)["jobs"][0]
            self.assertEqual(first["status"], "rate_limited")
            self.assertEqual(
                first["next_due_at"],
                reset.isoformat(),
            )

            clock.advance(3600)
            second = service.run_once()["jobs"][0]
            self.assertFalse(second["executed"])

            clock.advance(3601)
            third = service.run_once()["jobs"][0]
            self.assertTrue(third["executed"])

    def test_failed_job_uses_exponential_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            clock = Clock()
            service = CollectionService(
                config(db, [
                    job(
                        "bad",
                        "failing",
                        interval=600,
                        max_backoff_seconds=5000,
                    )
                ]),
                provider_factories={"failing": FailingProvider},
                now_fn=clock,
            )
            first = service.run_once(force=True)["jobs"][0]
            self.assertEqual(
                first["next_due_at"],
                (START + timedelta(seconds=1200)).isoformat(),
            )

            clock.advance(1200)
            second = service.run_once()["jobs"][0]
            self.assertEqual(second["status"], "failed")
            self.assertEqual(
                second["next_due_at"],
                (clock.value + timedelta(seconds=2400)).isoformat(),
            )

    def test_environment_secret_is_injected_but_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            captured = {}
            secret = "super-secret-oauth-token"
            service = CollectionService(
                config(db, [
                    job(
                        "auth-job",
                        "auth",
                        metadata={"site_url": "sc-domain:example.com"},
                        metadata_env={"access_token": "TEST_ACCESS_TOKEN"},
                    )
                ]),
                provider_factories={
                    "auth": lambda: AuthCaptureProvider(captured),
                },
                environ={"TEST_ACCESS_TOKEN": secret},
                now_fn=Clock(),
            )
            service.run_once(force=True)
            self.assertEqual(captured["access_token"], secret)
            self.assertEqual(captured["site_url"], "sc-domain:example.com")

            blob = db.read_bytes()
            self.assertNotIn(secret.encode("utf-8"), blob)
            self.assertNotIn(b"TEST_ACCESS_TOKEN", blob)

    def test_provider_raw_credentials_are_redacted_before_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            service = CollectionService(
                config(db, [job("leaky-job", "leaky")]),
                provider_factories={"leaky": LeakyProvider},
                environ={},
                now_fn=Clock(),
            )
            service.run_once(force=True)

            blob = db.read_bytes()
            for secret in (
                b"raw-secret",
                b"raw-cookie-secret",
                b"raw-client-secret",
            ):
                self.assertNotIn(secret, blob)

            conn = sqlite3.connect(db)
            raw_json = conn.execute(
                "SELECT raw_json FROM raw_events WHERE id='leaky-1'"
            ).fetchone()[0]
            conn.close()
            self.assertIn("[REDACTED]", raw_json)
            self.assertIn("safe-value", raw_json)
            self.assertIn("keep-me", raw_json)

    def test_missing_environment_variable_is_reported_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            captured = {}
            service = CollectionService(
                config(db, [
                    job(
                        "auth-job",
                        "auth",
                        metadata_env={"access_token": "MISSING_TOKEN"},
                    )
                ]),
                provider_factories={
                    "auth": lambda: AuthCaptureProvider(captured),
                },
                environ={},
                now_fn=Clock(),
            )
            row = service.run_once(force=True)["jobs"][0]
            self.assertTrue(
                any("MISSING_TOKEN" in warning for warning in row["warnings"])
            )
            self.assertNotIn("access_token", captured)
            # The environment-variable name may be persisted in diagnostic
            # warnings; it is not a credential. No secret value exists here.
            self.assertNotIn(b"access_token\":", db.read_bytes())

    def test_recent_events_round_trip_from_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            clock = Clock()
            service = CollectionService(
                config(db, [job("github-seed", "github")]),
                provider_factories={"github": StoredGitHubProvider},
                now_fn=clock,
            )
            service.run_once(force=True)

            with RadarStore(db) as store:
                rows = store.recent_events(
                    since=START - timedelta(hours=1),
                    providers=["github"],
                )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].id, "stored-github-1")
            self.assertEqual(rows[0].provider, "github")
            self.assertEqual(rows[0].metrics["score"], 12.0)
            self.assertEqual(rows[0].raw["topics"], ["context-engineering"])

    def test_radar_seed_uses_store_without_live_broad_collection(self):
        from radar import collect_seed_events

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            clock = Clock()
            service = CollectionService(
                config(db, [job("github-seed", "github")]),
                provider_factories={"github": StoredGitHubProvider},
                now_fn=clock,
            )
            service.run_once(force=True)

            seed = collect_seed_events(
                scope="AI",
                collector_db=str(db),
                collector_since_hours=24,
                providers=[FailingProvider()],
            )
            self.assertEqual(seed.source_mode, "collector_store")
            self.assertEqual(len(seed.events), 1)
            self.assertEqual(seed.events[0].id, "stored-github-1")
            self.assertEqual(seed.providers["github"]["status"], "stored")

    def test_disabled_job_is_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            disabled = job("off", "fake")
            disabled.enabled = False
            service = CollectionService(
                config(db, [disabled]),
                provider_factories={"fake": StaticProvider},
                now_fn=Clock(),
            )
            row = service.run_once()["jobs"][0]
            self.assertFalse(row["executed"])
            self.assertEqual(row["reason"], "not_due")


if __name__ == "__main__":
    unittest.main()
