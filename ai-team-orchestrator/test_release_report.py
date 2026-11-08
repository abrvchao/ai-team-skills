from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from release_report import generate_release_report, render_release_report


class ReleaseReportTests(unittest.TestCase):
    def sample_run(self, root: Path):
        implementation = root / "implementation.md"
        implementation.write_text(
            "## CHANGED_FILES\n- `app.py` — fixed behavior\n\n## TEST_RESULTS\n- Ran 12 tests ... OK\n",
            encoding="utf-8",
        )
        validation = root / "validation.md"
        validation.write_text("VERDICT: accept\n", encoding="utf-8")
        return {
            "run_id": "run_demo",
            "run_root": str(root),
            "goal": "Build a useful demo",
            "workspace": str(root / "product"),
            "status": "completed",
            "product_contract": {"acceptance": ["Demo runs", "Tests pass"]},
            "primary": {
                "dsh": {"role_id": "engineering_verifier", "worker_id": "dsh", "status": "completed", "artifact": str(root / "primary.md"), "attempts": []},
            },
            "implementation": {"artifact": str(implementation)},
            "validation": {"agent_id": "qwen", "status": "completed", "verdict": "accept", "artifact": str(validation)},
            "validation_history": [],
            "repairs": [],
            "errors": [],
            "resolved_errors": [],
            "implementation_validation_verdict": "accept",
            "progress": {"overall_percent": 100, "next_action": "ship it（交付）"},
        }

    def test_render_is_human_readable_and_truthful(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.sample_run(Path(tmp))
            report = render_release_report(run)
            self.assertIn("Release Report（发布报告）", report)
            self.assertIn("accept", report)
            self.assertIn("app.py", report)
            self.assertIn("Ran 12 tests", report)
            self.assertIn("ship it（交付）", report)

    def test_generate_writes_release_report_under_run_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.sample_run(Path(tmp))
            result = generate_release_report(run)
            path = Path(result["report_path"])
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "release_report.md")
            self.assertEqual(result["validation_verdict"], "accept")


if __name__ == "__main__":
    unittest.main()
