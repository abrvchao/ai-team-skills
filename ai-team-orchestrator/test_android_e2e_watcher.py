from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import android_e2e_watcher as watcher


class FakeClient:
    def __init__(self, root: Path):
        self.root = root
        self.submits = []
        self.task_status = "queued"
        self.started = False
        self.task_id = "task_android_e2e"
        self._artifacts = []

    def agents(self):
        return [{"agent_id": "android", "configured": True, "available": True, "queueable": True}]

    def submit(self, **kwargs):
        self.submits.append(kwargs)
        return {"task_id": self.task_id, "status": "queued", "started": False}

    def task(self, task_id):
        return {"task_id": task_id, "status": self.task_status, "started": self.started, "error": None}

    def artifacts(self, task_id):
        return list(self._artifacts)


class AndroidE2EWatcherTests(unittest.TestCase):
    def test_waits_without_authorized_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient(root)
            with patch.object(watcher, "authorized_devices", return_value=[]):
                state = watcher.step(client=client, state_path=root / "state.json", workspace=root / "work", adb_bin="adb")
            self.assertEqual(state["status"], "waiting_device")
            self.assertEqual(client.submits, [])

    def test_submits_once_and_reuses_task_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient(root)
            state_path = root / "state.json"
            with patch.object(watcher, "authorized_devices", return_value=["SERIAL1"]):
                first = watcher.step(client=client, state_path=state_path, workspace=root / "work", adb_bin="adb")
                second = watcher.step(client=client, state_path=state_path, workspace=root / "work", adb_bin="adb")
            self.assertEqual(first["status"], "submitted")
            self.assertEqual(second["status"], "submitted")
            self.assertEqual(len(client.submits), 1)
            self.assertEqual(client.submits[0]["metadata"]["action"], "inspect_device")

    def test_completed_task_requires_report_and_real_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient(root)
            state_path = root / "state.json"
            state_path.write_text(json.dumps({"status": "submitted", "task_id": client.task_id}), encoding="utf-8")
            workspace = root / "work"
            artifact_dir = workspace / ".ai-team-worker" / client.task_id
            artifact_dir.mkdir(parents=True)
            report = artifact_dir / "android_report.json"
            shot = artifact_dir / "android_screen.png"
            report.write_text(json.dumps({
                "mode": "read_only", "runtime": "adb", "serial": "SERIAL1",
                "android_version": "16", "screenshot_available": True,
            }), encoding="utf-8")
            shot.write_bytes(b"x" * 200)
            client.task_status = "completed"
            client.started = True
            client._artifacts = [
                {"path": f"workspace/.ai-team-worker/{client.task_id}/android_report.json"},
                {"path": f"workspace/.ai-team-worker/{client.task_id}/android_screen.png"},
            ]
            with patch.object(watcher, "authorized_devices", return_value=["SERIAL1"]):
                state = watcher.step(client=client, state_path=state_path, workspace=workspace, adb_bin="adb")
            self.assertEqual(state["status"], "passed")
            self.assertTrue(state["validation"]["ok"])


if __name__ == "__main__":
    unittest.main()
