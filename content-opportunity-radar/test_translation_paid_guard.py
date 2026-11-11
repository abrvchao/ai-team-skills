"""离线验证 Qwen 按量计费翻译必须主动显式授权。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from translation import TranslationService


class TranslationPaidGuardTests(unittest.TestCase):
    def create_service(self, folder):
        return TranslationService(
            cache_path=Path(folder) / "cache.sqlite3",
            model="synthetic-model",
            base_url="https://example.invalid/v1",
            api_key_file=Path(folder) / "does-not-exist",
        )

    def test_uncached_translation_never_opens_network_without_exact_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.create_service(tmp)
            for unauthorized in (None, "0", "yes", "true"):
                env = {} if unauthorized is None else {
                    "RADAR_TRANSLATION_PAID_API_APPROVED": unauthorized
                }
                with self.subTest(approval=unauthorized), patch.dict(os.environ, env, clear=True):
                    with patch("translation.request.urlopen") as network:
                        with self.assertRaisesRegex(ValueError, "paid_translation_not_approved"):
                            service.translate(target_language="zh", texts=["uncached sample"])
                        network.assert_not_called()

    def test_cached_translation_works_without_any_paid_key_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.create_service(tmp)
            service._store("ja", [("offline source", "オフライン翻訳")])
            with patch.dict(os.environ, {}, clear=True), patch("translation.request.urlopen") as network:
                result = service.translate(target_language="ja", texts=["offline source"])
                self.assertEqual(result["translations"], ["オフライン翻訳"])
                self.assertEqual(result["cache_hits"], 1)
                self.assertEqual(result["translated_count"], 0)
                network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
