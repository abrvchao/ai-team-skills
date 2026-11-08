from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bootstrap import prepare


ROOT = Path(__file__).resolve().parent


class DeployTests(unittest.TestCase):
    def test_prepare_initializes_all_schema_without_network_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.db"
            with patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
                first = prepare(database=str(db))
                second = prepare(database=str(db))

            self.assertTrue(db.is_file())
            self.assertFalse(first["network_used"])
            self.assertFalse(second["network_used"])
            self.assertIn("collector", first)
            self.assertIn("read_model", first)
            self.assertIn("workspaces", first)

    def test_dockerfile_is_non_root_and_has_no_secret_values(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim", text)
        self.assertIn("USER radar", text)
        self.assertNotIn("USER root", text)
        self.assertIn("HEALTHCHECK", text)
        for marker in (
            "ghp_",
            "Bearer ",
            "GSC_ACCESS_TOKEN=",
            "GOOGLE_ADS_ACCESS_TOKEN=",
        ):
            self.assertNotIn(marker, text)

    def test_compose_separates_web_worker_and_shares_volume(self):
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("radar-web:", text)
        self.assertIn("radar-worker:", text)
        self.assertGreaterEqual(text.count("radar-data:/data"), 2)
        self.assertIn("collector.py loop", text)
        self.assertIn("bootstrap.py prepare", text)
        self.assertIn('GITHUB_TOKEN: "${GITHUB_TOKEN:-}"', text)
        self.assertIn('GSC_ACCESS_TOKEN: "${GSC_ACCESS_TOKEN:-}"', text)
        self.assertIn('GOOGLE_ADS_ACCESS_TOKEN: "${GOOGLE_ADS_ACCESS_TOKEN:-}"', text)

    def test_dockerignore_excludes_local_secrets_and_state(self):
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn(".env", text)
        self.assertIn(".radar", text)
        self.assertIn("*.db", text)


if __name__ == "__main__":
    unittest.main()
