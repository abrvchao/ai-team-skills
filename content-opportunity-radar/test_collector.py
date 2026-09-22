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
            self.assertNotIn(b"MISSING_TOKEN", db.read_bytes())

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
