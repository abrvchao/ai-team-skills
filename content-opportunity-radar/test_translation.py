from __future__ import annotations

import http.client
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from api import create_handler
from read_model import OpportunityReadStore
from translation import TranslationService


class FakeTranslationService(TranslationService):
    def __init__(self, cache_path: Path) -> None:
        super().__init__(
            cache_path=cache_path,
            model="fake-model",
            base_url="https://example.invalid/v1",
            api_key_file=cache_path.with_name("unused-key"),
        )
        self.calls: list[tuple[str, list[str]]] = []

    def _call_provider(self, *, target_language: str, texts: list[str]) -> list[str]:
        self.calls.append((target_language, list(texts)))
        return [f"{target_language}:{text}" for text in texts]


class FakeActions:
    def __init__(self, service: TranslationService) -> None:
        self.service = service

    def translate(self, payload):
        return self.service.translate(
            target_language=payload.get("target_language"),
            texts=payload.get("texts"),
        )


class TranslationTests(unittest.TestCase):
    def test_translation_cache_avoids_repeat_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = FakeTranslationService(Path(tmp) / "cache.sqlite3")
            first = service.translate(
                target_language="zh",
                texts=["Hello world", "Radar opportunity"],
            )
            second = service.translate(
                target_language="zh",
                texts=["Hello world", "Radar opportunity"],
            )
            self.assertEqual(first["translated_count"], 2)
            self.assertEqual(first["cache_hits"], 0)
            self.assertEqual(second["translated_count"], 0)
            self.assertEqual(second["cache_hits"], 2)
            self.assertEqual(len(service.calls), 1)
            self.assertEqual(second["translations"][0], "zh:Hello world")

    def test_translation_input_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = FakeTranslationService(Path(tmp) / "cache.sqlite3")
            with self.assertRaises(ValueError):
                service.translate(target_language="xx", texts=["hello"])
            with self.assertRaises(ValueError):
                service.translate(target_language="zh", texts=["x"] * 41)
            with self.assertRaises(ValueError):
                service.translate(target_language="zh", texts=["x" * 1601])

    def test_local_http_translation_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with OpportunityReadStore(db):
                pass
            service = FakeTranslationService(Path(tmp) / "cache.sqlite3")
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(str(db), actions=FakeActions(service)),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1",
                    server.server_address[1],
                    timeout=5,
                )
                conn.request(
                    "POST",
                    "/v1/lite/translate",
                    body='{"target_language":"ja","texts":["Hello"]}',
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("ja:Hello", body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
