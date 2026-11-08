import importlib.util
import tempfile
import unittest
from pathlib import Path

from registry import AgentRegistry


def load_runtime():
    path = Path(__file__).parent / "computer-worker" / "computer_runtime.py"
    spec = importlib.util.spec_from_file_location("computer_runtime", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class ComputerWorkerTests(unittest.TestCase):
    def test_registry_exposes_computer_execution_surface(self):
        registry = AgentRegistry.default(dsh_configured=True, configured_pull_agents={"computer"})
        computer = registry.get("computer")
        self.assertTrue(computer.configured)
        self.assertTrue(computer.queueable)
        self.assertIn("gui_state_evidence", computer.capabilities)

    def test_target_must_stay_inside_workspace(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                runtime._resolve_target({"workspace": tmp, "metadata": {"path": "/etc/hosts"}})

    def test_relative_target_inside_workspace_is_allowed(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "proof.txt"
            target.write_text("proof", encoding="utf-8")
            self.assertEqual(
                runtime._resolve_target({"workspace": tmp, "metadata": {"path": "proof.txt"}}),
                target.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
