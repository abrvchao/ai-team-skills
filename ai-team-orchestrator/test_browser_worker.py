import json
import tempfile
import unittest
from pathlib import Path

from registry import AgentRegistry


class BrowserWorkerTests(unittest.TestCase):
    def test_registry_exposes_browser_as_pull_execution_surface(self):
        registry = AgentRegistry.default(
            dsh_configured=True,
            configured_pull_agents={"browser"},
        )
        browser = registry.get("browser")
        self.assertTrue(browser.configured)
        self.assertTrue(browser.queueable)
        self.assertEqual(browser.transport, "registered_pull")
        self.assertIn("browser_navigation", browser.capabilities)
        self.assertIn("screenshot", browser.capabilities)

    def test_browser_runtime_rejects_non_http_url(self):
        import importlib.util
        module_path = Path(__file__).parent / "browser-worker" / "browser_runtime.py"
        spec = importlib.util.spec_from_file_location("browser_runtime", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module._safe_url({"metadata": {"url": "file:///etc/passwd"}})

    def test_browser_runtime_prefers_structured_url(self):
        import importlib.util
        module_path = Path(__file__).parent / "browser-worker" / "browser_runtime.py"
        spec = importlib.util.spec_from_file_location("browser_runtime2", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        self.assertEqual(
            module._safe_url({"metadata": {"url": "https://example.com"}, "prompt": "https://wrong.example"}),
            "https://example.com",
        )


if __name__ == "__main__":
    unittest.main()
