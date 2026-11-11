from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from build_release_bundle import build, verify


class ReleaseBundleTests(unittest.TestCase):
    def test_bundle_is_deterministic_and_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.zip"
            b = Path(tmp) / "b.zip"
            first = build(a)
            second = build(b)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(), first["sha256"])
            self.assertTrue(verify(a)["ok"])
            self.assertGreater(first["file_count"], 10)

    def test_runtime_and_secret_paths_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle.zip"
            build(out)
            import zipfile
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            forbidden = ("node_modules/", ".venv/", ".orchestrator/", ".ai-team-runs/")
            self.assertFalse(any(any(token in name for token in forbidden) for name in names))


if __name__ == "__main__":
    unittest.main()
