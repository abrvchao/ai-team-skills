"""H2 写入边界回归：只用临时 SQLite/缓存和合成签名，不发外部请求。"""
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from collector import RadarStore
from core import CollectionResult, EventCache, ProviderState
from event_persistence import EventPersistenceDenied, PREVIEW_SOURCE
from source_promotion import preview_raw_event
from test_authority_fixtures import SyntheticAuthority
from test_collector import START, job, make_event
from test_source_promotion import NOW, fixtures


class ProductionPersistenceGateTests(unittest.TestCase):
    def setUp(self):
        self.authority = SyntheticAuthority()
        self.addCleanup(self.authority.close)
        grant, staged, review = fixtures(self.authority)
        self.preview = preview_raw_event(staged, grant, review, now=NOW)
        self.assertFalse(self.preview.persistence_allowed)
        self.assertEqual(self.preview.event.source, PREVIEW_SOURCE)

    def record(self, store, events):
        return store.record_result(
            job(),
            CollectionResult(provider="fake", status=ProviderState.HEALTHY,
                             events=events, collected_at=START),
            started_at=START, finished_at=START,
            next_due_at=START + timedelta(minutes=10), consecutive_failures=0,
        )

    def counts(self, store):
        return tuple(store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("raw_events", "event_observations",
                                   "metric_snapshots", "collection_runs", "job_state"))

    def test_preview_wrapper_and_derived_event_rejected_before_sql(self):
        with tempfile.TemporaryDirectory() as tmp, RadarStore(Path(tmp) / "radar.db") as store:
            for item in (self.preview, self.preview.event):
                with self.subTest(item=type(item).__name__), self.assertRaises(
                    EventPersistenceDenied
                ):
                    self.record(store, [item])
                self.assertEqual(self.counts(store), (0, 0, 0, 0, 0))

    def test_mixed_batch_is_atomic_and_does_not_store_valid_prefix(self):
        with tempfile.TemporaryDirectory() as tmp, RadarStore(Path(tmp) / "radar.db") as store:
            with self.assertRaisesRegex(EventPersistenceDenied, "offline_preview"):
                self.record(store, [make_event(), self.preview.event])
            self.assertEqual(self.counts(store), (0, 0, 0, 0, 0))
            self.record(store, [make_event()])
            self.assertEqual(self.counts(store), (1, 1, 3, 1, 1))

    def test_direct_upsert_rejects_preview_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp, RadarStore(Path(tmp) / "radar.db") as store:
            with self.assertRaises(EventPersistenceDenied):
                store._upsert_event(self.preview.event, observed_at=START, job_id="test")
            self.assertEqual(self.counts(store), (0, 0, 0, 0, 0))

    def test_database_triggers_reject_direct_insert_and_update(self):
        with tempfile.TemporaryDirectory() as tmp, RadarStore(Path(tmp) / "radar.db") as store:
            self.record(store, [make_event()])
            with self.assertRaisesRegex(sqlite3.IntegrityError, "offline_preview"):
                store.conn.execute(
                    """INSERT INTO raw_events SELECT ?, provider, ?, acquisition_method,
                       external_id, url, title, text, author, community, published_at,
                       first_seen_at, last_seen_at, retrieval_count, language, country,
                       metrics_json, raw_json, provenance_json
                       FROM raw_events WHERE id = ?""",
                    ("synthetic-direct-preview", PREVIEW_SOURCE, "event-1"),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "offline_preview"):
                store.conn.execute("UPDATE raw_events SET source=? WHERE id=?",
                                   (PREVIEW_SOURCE, "event-1"))
            self.assertEqual(store.conn.execute(
                "SELECT source FROM raw_events WHERE id='event-1'"
            ).fetchone()[0], "example.test")
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0], 1)

    def test_existing_database_gets_sql_triggers_on_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing.db"
            with RadarStore(path) as store:
                self.record(store, [make_event()])
                store.conn.execute("DROP TRIGGER reject_offline_preview_insert")
                store.conn.execute("DROP TRIGGER reject_offline_preview_update")
                store.conn.commit()
            with RadarStore(path) as store:
                triggers = {row[0] for row in store.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'")}
                self.assertIn("reject_offline_preview_insert", triggers)
                self.assertIn("reject_offline_preview_update", triggers)
                with self.assertRaises(sqlite3.IntegrityError):
                    store.conn.execute("UPDATE raw_events SET source=? WHERE id='event-1'",
                                       (PREVIEW_SOURCE,))

    def test_file_cache_fails_closed_without_corrupting_prior_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            cache = EventCache(path)
            for candidate in (self.preview, self.preview.event):
                with self.subTest(candidate=type(candidate).__name__), self.assertRaises(
                    EventPersistenceDenied
                ):
                    cache.save("zhihu", "test", [candidate])
                self.assertFalse(path.exists())
            cache.save("github", "AI", [make_event()])
            before = path.read_bytes()
            with self.assertRaises(EventPersistenceDenied):
                cache.save("github", "AI", [make_event(), self.preview.event])
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
