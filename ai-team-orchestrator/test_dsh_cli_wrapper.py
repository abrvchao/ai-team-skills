"""DSH 包装器离线回归：仅启动 Python 本地子进程，不连接模型。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from dsh_cli_wrapper import child_environment, render_argv, wrapper_timeout

WRAPPER = Path(__file__).with_name("dsh_cli_wrapper.py")


class DshWrapperOfflineTests(unittest.TestCase):
    def run_wrapper(self, request, inner, *, timeout="5", input_text=None):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "request.json"
            path.write_text(input_text if input_text is not None else json.dumps(request),
                            encoding="utf-8")
            env = {**os.environ, "DSH_INNER_COMMAND_JSON": json.dumps(inner),
                   "DSH_WRAPPER_TIMEOUT_SECONDS": timeout,
                   "FAKE_SECRET_TOKEN": "never_pass_to_child"}
            return subprocess.run([sys.executable, str(WRAPPER), str(path)],
                                  text=True, capture_output=True, env=env,
                                  timeout=12)

    def test_malformed_requests_emit_failed_instead_of_traceback(self):
        valid = {"workspace": "/tmp", "task_id": "offline-test"}
        for value, raw in [(None, "{"), ({"workspace": "/tmp"}, None),
                           ({"task_id": "x"}, None), ({}, None), (valid, "")]:
            with self.subTest(value=value, raw=raw):
                result = self.run_wrapper(value, [sys.executable, "-c", "pass"],
                                          input_text=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('"status": "failed"', result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_unsupported_placeholder_is_terminal_failed(self):
        result = self.run_wrapper({"workspace": "/tmp", "task_id": "offline-test"},
                                  [sys.executable, "-c", "pass", "{oops}"])
        self.assertEqual(result.returncode, 2)
        self.assertIn('"status": "failed"', result.stdout)
        self.assertNotIn('"status": "running"', result.stdout)

    def test_timeout_terminates_local_process_and_reports_failed(self):
        started = time.monotonic()
        result = self.run_wrapper({"workspace": "/tmp", "task_id": "offline-test"},
                                  [sys.executable, "-c", "import time; time.sleep(30)"],
                                  timeout="1")
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(result.returncode, 124)
        self.assertIn('"error": "dsh_wrapper_timeout"', result.stdout)
        self.assertNotIn('"status": "completed"', result.stdout)

    def test_secret_env_is_not_forwarded(self):
        script = 'import os; print(os.environ.get("FAKE_SECRET_TOKEN", "missing"))'
        result = self.run_wrapper({"workspace": "/tmp", "task_id": "offline-test"},
                                  [sys.executable, "-c", script])
        self.assertEqual(result.returncode, 0)
        self.assertIn("missing", result.stderr)
        self.assertNotIn("never_pass_to_child", result.stderr)
        self.assertIn('"status": "completed"', result.stdout)

    def test_timeout_and_template_validation(self):
        self.assertNotIn("FAKE_SECRET_TOKEN", child_environment())
        with self.assertRaises(ValueError):
            render_argv('["{unknown}"]', {"workspace": "/tmp"})
        with self.assertRaises(ValueError):
            render_argv('["{workspace.__class__}"]', {"workspace": "/tmp"})
        with self.assertRaises(ValueError):
            render_argv('["{workspace:>30}"]', {"workspace": "/tmp"})
        self.assertGreater(wrapper_timeout(), 0)


if __name__ == "__main__":
    unittest.main()
