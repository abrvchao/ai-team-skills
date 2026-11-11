from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from check_task_truth import declared_artifacts, validate_manifest


class TaskTruthTests(unittest.TestCase):
    def test_declared_artifacts_ignores_descriptive_optional_output(self):
        task = {
            "outputs": [
                "docs/research/result.md",
                "small sanitized sample fixture if legally/technically available",
            ]
        }
        self.assertEqual(declared_artifacts(task), ["docs/research/result.md"])

    def test_completed_requires_existing_tracked_result_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "tasks": [
                    {
                        "task_id": "T1",
                        "status": "completed_artifact_present",
                        "result": {"artifact": "docs/result.md"},
                    }
                ]
            }
            errors = validate_manifest(
                manifest,
                repo_root=root,
                is_tracked=lambda _path: False,
            )
            self.assertIn("completed artifact does not exist", errors[0])

            path = root / "docs/result.md"
            path.parent.mkdir(parents=True)
            path.write_text("ok", encoding="utf-8")
            errors = validate_manifest(
                manifest,
                repo_root=root,
                is_tracked=lambda _path: False,
            )
            self.assertIn("not tracked by git", errors[0])

            self.assertEqual(
                validate_manifest(
                    manifest,
                    repo_root=root,
                    is_tracked=lambda path: path == "docs/result.md",
                ),
                [],
            )

    def test_queued_task_with_tracked_artifact_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "docs/already.md"
            path.parent.mkdir(parents=True)
            path.write_text("done", encoding="utf-8")
            manifest = {
                "tasks": [
                    {
                        "task_id": "T2",
                        "status": "queued_not_started",
                        "outputs": ["docs/already.md"],
                    }
                ]
            }
            errors = validate_manifest(
                manifest,
                repo_root=root,
                is_tracked=lambda _path: True,
            )
            self.assertEqual(len(errors), 1)
            self.assertIn("status=queued_not_started", errors[0])


if __name__ == "__main__":
    unittest.main()
