import importlib.util
import unittest
from pathlib import Path

from registry import AgentRegistry


def load_runtime():
    path = Path(__file__).parent / "android-worker" / "android_runtime.py"
    spec = importlib.util.spec_from_file_location("android_runtime", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class AndroidWorkerTests(unittest.TestCase):
    def test_registry_exposes_android_execution_surface(self):
        registry = AgentRegistry.default(dsh_configured=True, configured_pull_agents={"android"})
        android = registry.get("android")
        self.assertTrue(android.configured)
        self.assertTrue(android.queueable)
        self.assertIn("android_screenshot", android.capabilities)

    def test_parse_devices_keeps_only_authorized_devices(self):
        runtime = load_runtime()
        output = (
            "List of devices attached\n"
            "ABC123               device product:foo model:Pixel_8 device:shiba transport_id:1\n"
            "XYZ999\tunauthorized usb:1-1 transport_id:2\n"
        )
        devices = runtime.parse_devices(output)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["serial"], "ABC123")
        self.assertEqual(devices[0]["model"], "Pixel_8")


if __name__ == "__main__":
    unittest.main()
