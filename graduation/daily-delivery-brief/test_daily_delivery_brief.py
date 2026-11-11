#!/usr/bin/env python3
"""Automated tests for the Daily Delivery Brief CLI (read-only).

Covers the acceptance-critical cases:
* normal active-run output (golden file + field assertions);
* a blocked / late run;
* missing data -> explicit ``EVIDENCE GAP`` markers, no fabricated content;
* run-JSON artifact records, including a recorded-but-missing artifact;
* read-only behaviour (source truth files are never modified);
* clean non-zero exit when the run root cannot be resolved.

Runs with the stdlib only: ``python3 -m unittest -v`` (pytest also discovers it).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parent
CLI = PRODUCT_DIR / "daily_delivery_brief.py"
FIXTURES = PRODUCT_DIR / "fixtures"
EXPECTED = FIXTURES / "expected"
GENERATED_AT = "2026-10-06T06:00:00+00:00"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=PRODUCT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def snapshot(directory: Path) -> dict[str, str]:
    """Hash every file under ``directory`` so tests can prove read-only use."""
    digest: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest[str(path.relative_to(directory))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digest


class ActiveRunTests(unittest.TestCase):
    def test_active_run_matches_expected_golden(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--status-file",
            "fixtures/active/status.md",
            "--generated-at",
            GENERATED_AT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, (EXPECTED / "active.md").read_text(encoding="utf-8")
        )

    def test_active_run_reports_every_required_field(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--status-file",
            "fixtures/active/status.md",
            "--generated-at",
            GENERATED_AT,
        )
        for field in (
            "**Current Priority**",
            "**Goal**",
            "**Phase / Status**",
            "**Schedule State**",
            "**Project Due At**",
            "**Planned / Assigned Workers**",
            "**Blockers**",
            "**Next Action**",
            "**Delivered Artifacts**",
        ):
            self.assertIn(field, result.stdout, field)

    def test_active_run_lists_only_existing_artifacts(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--status-file",
            "fixtures/active/status.md",
        )
        self.assertIn("- primary/dsh/primary.md", result.stdout)
        self.assertIn("- implementation/implementation_report.md", result.stdout)
        # qwen never produced an artifact in this fixture: it must not be listed.
        self.assertNotIn("primary/qwen/primary.md", result.stdout)

    def test_run_json_artifact_records_match_expected(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--run-json",
            "fixtures/active/orchestrator_run.json",
            "--status-file",
            "fixtures/active/status.md",
            "--generated-at",
            GENERATED_AT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            (EXPECTED / "active_runjson.md").read_text(encoding="utf-8"),
        )

    def test_recorded_but_missing_artifact_is_an_evidence_gap(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--run-json",
            "fixtures/active/orchestrator_run_missing_artifact.json",
            "--status-file",
            "fixtures/active/status.md",
            "--generated-at",
            GENERATED_AT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            (EXPECTED / "missing_artifact.md").read_text(encoding="utf-8"),
        )
        self.assertIn("recorded but missing", result.stdout)
        self.assertIn("EVIDENCE GAP", result.stdout)
        # The failed qwen artifact must not be presented as delivered.
        self.assertNotIn("- primary/qwen/primary.md", result.stdout)


class BlockedAndMissingDataTests(unittest.TestCase):
    def test_blocked_late_run_matches_expected(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/blocked",
            "--status-file",
            "fixtures/blocked/status.md",
            "--generated-at",
            GENERATED_AT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, (EXPECTED / "blocked.md").read_text(encoding="utf-8")
        )
        self.assertIn("**Phase / Status**: validation / blocked", result.stdout)
        self.assertIn("**Schedule State**: late", result.stdout)
        self.assertIn("worker quota exhausted", result.stdout)

    def test_missing_data_emits_evidence_gaps_without_fabrication(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/missing-data",
            "--status-file",
            "fixtures/missing-data/status.md",
            "--generated-at",
            GENERATED_AT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            (EXPECTED / "missing_data.md").read_text(encoding="utf-8"),
        )
        self.assertIn("**Goal**: EVIDENCE GAP: delivery_plan.json#goal", result.stdout)
        self.assertIn("EVIDENCE GAP: delivered artifacts", result.stdout)
        self.assertIn("EVIDENCE GAP: fixtures/missing-data/status.md", result.stdout)
        # No fabricated artifact or blocker text may appear.
        self.assertNotIn("primary/grok/primary.md", result.stdout)
        self.assertNotIn("Content Opportunity Radar usefulness", result.stdout)

    def test_missing_status_file_is_a_gap_not_a_guess(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--status-file",
            "fixtures/does-not-exist.md",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "**Current Priority**: EVIDENCE GAP: fixtures/does-not-exist.md",
            result.stdout,
        )

    def test_malformed_json_downgrades_to_evidence_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            (run_root / "progress.json").write_text("{ not valid json", encoding="utf-8")
            (run_root / "delivery_plan.json").write_text(
                json.dumps({"goal": "g", "participants": ["dsh"]}), encoding="utf-8"
            )
            result = run_cli("--run-root", str(run_root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("## Source Errors", result.stdout)
            self.assertIn("EVIDENCE GAP: progress.json (invalid JSON", result.stdout)


class ReadOnlyAndErrorTests(unittest.TestCase):
    def test_source_truth_files_are_never_modified(self) -> None:
        before = {
            "active": snapshot(FIXTURES / "active"),
            "blocked": snapshot(FIXTURES / "blocked"),
            "missing-data": snapshot(FIXTURES / "missing-data"),
        }
        for run_root, extra in (
            ("fixtures/active", ["--run-json", "fixtures/active/orchestrator_run.json"]),
            ("fixtures/blocked", []),
            ("fixtures/missing-data", []),
        ):
            result = run_cli("--run-root", run_root, *extra)
            self.assertEqual(result.returncode, 0, result.stderr)
        after = {
            "active": snapshot(FIXTURES / "active"),
            "blocked": snapshot(FIXTURES / "blocked"),
            "missing-data": snapshot(FIXTURES / "missing-data"),
        }
        self.assertEqual(before, after)

    def test_output_flag_writes_only_the_requested_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "brief.md"
            before = snapshot(FIXTURES / "active")
            result = run_cli(
                "--run-root",
                "fixtures/active",
                "--status-file",
                "fixtures/active/status.md",
                "--generated-at",
                GENERATED_AT,
                "--output",
                str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                (EXPECTED / "active.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(before, snapshot(FIXTURES / "active"))

    def test_unresolvable_run_root_exits_nonzero_without_traceback(self) -> None:
        result = run_cli("--run-root", "fixtures/definitely-not-a-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_run_json_exits_nonzero_without_traceback(self) -> None:
        result = run_cli(
            "--run-root",
            "fixtures/active",
            "--run-json",
            "fixtures/does-not-exist.json",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_default_invocation_discovers_the_latest_run(self) -> None:
        """A bare invocation from the workspace must work without flags."""
        result = run_cli("--generated-at", GENERATED_AT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# Daily Delivery Brief — run_", result.stdout)
        self.assertIn("**Phase / Status**:", result.stdout)

    def test_cli_source_has_no_machine_specific_paths(self) -> None:
        source = CLI.read_text(encoding="utf-8")
        self.assertNotIn("/Volumes/", source)
        self.assertNotIn("/Users/", source)


class ParserUnitTests(unittest.TestCase):
    def test_priority_parser_returns_first_item(self) -> None:
        sys.path.insert(0, str(PRODUCT_DIR))
        try:
            import daily_delivery_brief as ddb
        finally:
            sys.path.pop(0)
        text = (
            "# AI Team Current Status\n\n"
            "## Current execution priority（当前执行优先级）\n\n"
            "1. M3 V1 Final Graduation — current。\n"
            "2. Next thing.\n"
        )
        self.assertEqual(
            ddb.parse_priority(text), "M3 V1 Final Graduation — current。"
        )
        self.assertIsNone(ddb.parse_priority("## Other\n\n1. not priority\n"))

    def test_evidence_gap_marker_format(self) -> None:
        sys.path.insert(0, str(PRODUCT_DIR))
        try:
            import daily_delivery_brief as ddb
        finally:
            sys.path.pop(0)
        self.assertEqual(
            ddb.evidence_gap("delivery_plan.json#goal"),
            "EVIDENCE GAP: delivery_plan.json#goal",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
