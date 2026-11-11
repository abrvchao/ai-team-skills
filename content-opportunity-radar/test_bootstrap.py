from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_init_creates_public_only_config_and_does_not_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "plan.json"
            db = Path(tmp) / "radar.db"

            first = bootstrap.write_local_config(
                path=config,
                scope="AI",
                database=str(db),
            )
            self.assertTrue(first["created"])

            payload = json.loads(config.read_text(encoding="utf-8"))
            providers = [job["provider"] for job in payload["jobs"]]
            self.assertEqual(
                providers,
                ["github", "hackernews", "google_news", "gdelt"],
            )
            self.assertNotIn("gsc", providers)
            self.assertNotIn("keyword_planner", providers)

            config.write_text('{"sentinel": true}\n', encoding="utf-8")
            second = bootstrap.write_local_config(
                path=config,
                scope="AI Agents",
                database=str(db),
            )
            self.assertFalse(second["created"])
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8")),
                {"sentinel": True},
            )

    def test_doctor_reports_secret_presence_only_never_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = "super-secret-github-token"
            result = bootstrap.doctor(
                config_path=Path(tmp) / "plan.json",
                database=str(Path(tmp) / "radar.db"),
                environ={
                    "GITHUB_TOKEN": secret,
                    "GSC_ACCESS_TOKEN": "",
                    "GOOGLE_ADS_ACCESS_TOKEN": "another-secret",
                },
            )
            encoded = json.dumps(result)
            self.assertTrue(result["optional_credentials_present"]["GITHUB_TOKEN"])
            self.assertFalse(result["optional_credentials_present"]["GSC_ACCESS_TOKEN"])
            self.assertNotIn(secret, encoded)
            self.assertNotIn("another-secret", encoded)

    def test_demo_continues_when_one_provider_is_degraded(self):
        class FakeService:
            def __init__(self, config):
                self.config = config

            def run_once(self, force=False):
                self.force = force
                return {
                    "jobs": [
                        {
                            "provider": "github",
                            "status": "healthy",
                            "event_count": 10,
                            "warnings": [],
                        },
                        {
                            "provider": "gdelt",
                            "status": "degraded",
                            "event_count": 0,
                            "warnings": ["TLS unavailable"],
                        },
                    ]
                }

        fake_radar = {
            "top_opportunities": [
                {
                    "topic": "Agent Memory",
                    "rank_score": 84.0,
                    "opportunity": {
                        "score": 82.0,
                        "components": {"confidence": 78.0},
                    },
                }
            ],
            "read_model": {
                "database": "radar.db",
                "run_id": "run-1",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "plan.json"
            db = Path(tmp) / "radar.db"
            bootstrap.write_local_config(
                path=config,
                scope="AI",
                database=str(db),
            )

            with patch.object(bootstrap, "CollectionService", FakeService), patch.object(
                bootstrap,
                "run_discovery",
                return_value=fake_radar,
            ):
                result = bootstrap.demo(
                    scope="AI",
                    config_path=config,
                    database=str(db),
                )

            self.assertEqual(result["status"], "ready")
            statuses = {
                row["provider"]: row["status"]
                for row in result["providers"]
            }
            self.assertEqual(statuses["github"], "healthy")
            self.assertEqual(statuses["gdelt"], "degraded")
            self.assertEqual(result["top_opportunities"][0]["topic"], "Agent Memory")

    def test_radar_passes_collector_and_read_model_db_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "radar.db")
            with patch.object(
                bootstrap,
                "run_discovery",
                return_value={"top_opportunities": []},
            ) as mocked:
                bootstrap.radar(
                    scope="AI Agents",
                    database=db,
                    top_n=3,
                )

            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs["collector_db"], db)
            self.assertEqual(kwargs["read_model_db"], db)
            self.assertTrue(kwargs["include_research_pack"])
            self.assertEqual(kwargs["top_n"], 3)

    def test_public_output_scrubs_secret_shaped_keys_and_strings(self):
        secret_payload = {
            "access_token": "abc123",
            "warning": "Authorization: Bearer very-secret",
            "nested": {
                "cookie": "session-secret",
                "note": "safe",
            },
        }
        output = bootstrap._public(secret_payload)
        encoded = json.dumps(output)
        self.assertNotIn("abc123", encoded)
        self.assertNotIn("very-secret", encoded)
        self.assertNotIn("session-secret", encoded)
        self.assertIn("[REDACTED]", encoded)
        self.assertIn("safe", encoded)

    def test_serve_delegates_to_existing_api_server(self):
        with patch.object(bootstrap, "serve_api") as serve:
            bootstrap.serve_api(
                database="demo.db",
                host="127.0.0.1",
                port=8787,
            )
        serve.assert_called_once_with(
            database="demo.db",
            host="127.0.0.1",
            port=8787,
        )

    def test_print_never_emits_secret_value(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            bootstrap._print({
                "refresh_token": "secret-value",
                "warning": "token=another-secret",
            })
        output = buffer.getvalue()
        self.assertNotIn("secret-value", output)
        self.assertNotIn("another-secret", output)
        self.assertIn("[REDACTED]", output)


if __name__ == "__main__":
    unittest.main()
