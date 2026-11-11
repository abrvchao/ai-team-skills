import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from product_intake import ProductContractStore

import project_dashboard


class ProjectDashboardTests(unittest.TestCase):
    def test_social_sources_schedule_not_claimed_as_completed(self):
        plan = project_dashboard._social_source_expansion()
        self.assertEqual(plan["status"], "scheduled_not_implemented")
        self.assertEqual(len(plan["platforms"]), 5)
        self.assertEqual(len(plan["phases"]), 4)
        self.assertEqual(len(plan["work_packages"]), 4)
        # 调度器可能在测试期间把“待派工”更新为“执行中/阻塞”；
        # 不再把动态业务状态硬编码成某个固定快照。
        self.assertTrue(all(item["id"] and item["owner"] and item["status"]
                            for item in plan["work_packages"]))
        self.assertIn('id="social-sources"', project_dashboard.HTML)
        self.assertIn("d.social_sources", project_dashboard.HTML)

    def test_monitor_dashboard_has_separate_project_and_worker_views(self):
        self.assertIn('id="monitor-runs"', project_dashboard.HTML)
        self.assertIn('id="monitor-workers"', project_dashboard.HTML)
        self.assertIn("/api/monitor", project_dashboard.HTML)
        self.assertIn("taskStatusRow", project_dashboard.HTML)

    def test_worker_changes_and_handoffs_are_exposed_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = project_dashboard.RUN_STATE_DIR
            project_dashboard.RUN_STATE_DIR = Path(tmp)
            try:
                (Path(tmp) / "_worker-roster-events.jsonl").write_text(
                    json.dumps({"agent_id": "workbuddy", "kind": "worker_online",
                                "detected_at": "2026-10-11T12:00:00Z",
                                "token": "DO_NOT_LEAK"}) + "\n"
                )
                (Path(tmp) / "run_handoff.json").write_text(json.dumps({
                    "run_id": "run_handoff", "status": "primary_running",
                    "phase": "primary", "goal": "handoff state",
                    "progress": {"handoffs": {
                        "total": 1, "pending": 1, "in_progress": 0,
                        "delivered": 0, "failed": 0,
                        "latest": [{"handoff_id": "handoff_1",
                                    "from_worker": "dsh", "to_worker": "qwen",
                                    "status": "assigned"}]}}}))
                changes = project_dashboard._worker_changes()
                self.assertEqual(len(changes), 1)
                self.assertNotIn("DO_NOT_LEAK", json.dumps(changes))
                handoff = project_dashboard._latest_runs()[0]["handoffs"]
                self.assertEqual(handoff["total"], 1)
                self.assertEqual(handoff["latest"][0]["to_worker"], "qwen")
                self.assertIn("handoff-board", project_dashboard.HTML)
                self.assertIn("renderHandoffs(d)", project_dashboard.HTML)
            finally:
                project_dashboard.RUN_STATE_DIR = original
    def test_recent_tasks_projects_truth_and_hides_prompt(self):
        class FakeBridge:
            def __init__(self, url):
                pass
            def tasks(self):
                return [
                    {"task_id": "task_old", "agent_id": "qwen", "title": "旧任务",
                     "status": "queued", "started": False,
                     "created_at": "2026-10-01T00:00:00+00:00", "prompt": "private"},
                    {"task_id": "task_new", "agent_id": "mimo", "title": "新任务",
                     "status": "completed", "started": True,
                     "created_at": "2026-10-11T00:00:00+00:00", "prompt": "secret"},
                ]
        with patch.object(project_dashboard, "BridgeClient", FakeBridge):
            result = project_dashboard._recent_tasks("http://bridge")
        self.assertTrue(result["available"])
        self.assertEqual([x["task_id"] for x in result["items"]], ["task_new", "task_old"])
        self.assertTrue(result["items"][0]["started"])
        self.assertNotIn("prompt", result["items"][0])

    def test_task_detail_reads_real_events_and_artifacts_without_messages(self):
        class FakeBridge:
            def __init__(self, url):
                pass
            def task(self, task_id):
                return {"task_id": task_id, "status": "completed",
                        "started": True, "messages": [{"text": "secret"}]}
            def progress(self, task_id):
                return [{"stage": "review", "percent": 75, "message": "通过",
                         "created_at": "2026-10-11T00:00:00Z", "token": "secret"}]
            def artifacts(self, task_id):
                return [{"artifact_id": "art1", "label": "报告", "kind": "file",
                         "path": "report.md", "metadata": {"secret": "x"}}]
        with patch.object(project_dashboard, "BridgeClient", FakeBridge):
            detail = project_dashboard._task_detail("http://bridge", "task_good")
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["progress"][0]["percent"], 75)
        self.assertEqual(detail["artifacts"][0]["path"], "report.md")
        self.assertNotIn("messages", detail)
        self.assertNotIn("token", detail["progress"][0])
        self.assertNotIn("metadata", detail["artifacts"][0])
        with self.assertRaises(ValueError):
            project_dashboard._task_detail("http://bridge", "../../etc/passwd")

    def test_agent_list_includes_mimo_and_all_registered(self):
        class FakeBridge:
            def __init__(self, url):
                pass
            def agents(self):
                return [{"agent_id": "mimo", "available": False},
                        {"agent_id": "qwen", "available": True}]
        with patch.object(project_dashboard, "BridgeClient", FakeBridge):
            agents = project_dashboard._agents("http://bridge")
        self.assertEqual([x["agent_id"] for x in agents], ["mimo", "qwen"])

    def test_program_state_reads_all_four_milestones(self):
        state = project_dashboard._program_state()
        self.assertEqual([x["id"] for x in state["milestones"]], ["M0", "M1", "M2", "M3"])
        # 中文说明：总进度来自状态真相源，测试不能写死某个历史百分比。
        self.assertRegex(state["weighted_progress"], r"^~?\d+%$")
        self.assertNotEqual(state["weighted_progress"], "unknown（未知）")

    def test_product_state_exposes_current_gate_and_stages(self):
        state = project_dashboard._product_state()
        self.assertIn("Radar", state["name"])
        # 不能写死阶段编号：进展文档已进入 Operational Use 等后续阶段。
        self.assertNotEqual(state["gate"], "unknown（未知）")
        self.assertTrue(state["gate"].strip())
        self.assertGreaterEqual(len(state["stages"]), 5)

    def test_latest_runs_exposes_schedule_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = project_dashboard.RUN_STATE_DIR
            try:
                root = Path(tmp)
                project_dashboard.RUN_STATE_DIR = root
                (root / "run_x.json").write_text(json.dumps({
                    "run_id": "run_x",
                    "goal": "deliver a tiny result",
                    "status": "primary_running",
                    "phase": "primary",
                    "progress": {
                        "overall_percent": 20,
                        "schedule": {
                            "state": "on_track",
                            "project_due_at": "2026-10-06T04:00:00+00:00",
                        },
                        "next_action": "finish primary",
                        "blockers": [],
                    },
                    "errors": [],
                    "updated_at": "2026-10-06T03:00:00+00:00",
                }), encoding="utf-8")
                rows = project_dashboard._latest_runs()
                self.assertEqual(rows[0]["schedule_state"], "on_track")
                self.assertEqual(rows[0]["project_due_at"], "2026-10-06T04:00:00+00:00")
            finally:
                project_dashboard.RUN_STATE_DIR = original



    def test_r3_readiness_projects_machine_truth_without_recomputing_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = project_dashboard.R3_READINESS_FILE
            try:
                path = Path(tmp) / "R3_READINESS.json"
                path.write_text(json.dumps({
                    "preparation_complete": True,
                    "stage_progress": "0/4",
                    "stage_percent": 0,
                    "r3_complete": False,
                    "gates": [{"id":"human_precision","label":"Human precision","passed":False,"blocker":"human labels required"}],
                    "next_actions": ["human labels required"],
                }), encoding="utf-8")
                project_dashboard.R3_READINESS_FILE = path
                state = project_dashboard._r3_readiness_state()
                self.assertTrue(state["available"])
                self.assertTrue(state["preparation_complete"])
                self.assertEqual(state["stage_progress"], "0/4")
                self.assertFalse(state["r3_complete"])
                self.assertFalse(state["gates"][0]["passed"])
            finally:
                project_dashboard.R3_READINESS_FILE = original

    def test_contract_state_exposes_approval_without_copying_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = project_dashboard.CONTRACT_DIR
            try:
                project_dashboard.CONTRACT_DIR = Path(tmp)
                payload = {
                    "contract_id": "contract_demo",
                    "product_name": "Demo Product",
                    "idea": "做一个演示产品",
                    "status": "draft",
                    "approval_required": True,
                    "approved": False,
                    "run_id": None,
                    "created_at": "2026-10-04T00:00:00+00:00",
                }
                (Path(tmp) / "contract_demo.json").write_text(json.dumps(payload), encoding="utf-8")
                rows = project_dashboard._contracts_state()
                self.assertEqual(rows[0]["contract_id"], "contract_demo")
                self.assertTrue(rows[0]["approval_required"])
                self.assertFalse(rows[0]["approved"])
            finally:
                project_dashboard.CONTRACT_DIR = original


    def test_dashboard_contract_decision_requires_action_header_and_explicit_boolean(self):
        class FakeTeam:
            def __init__(self):
                self.calls = []
            def start_run(self, **kwargs):
                self.calls.append(kwargs)
                return {"run_id": "run_ui_001", "status": "primary_running"}

        with tempfile.TemporaryDirectory() as tmp:
            store = ProductContractStore(Path(tmp) / "contracts")
            draft = store.draft(idea="做一个通过网页批准后才启动的产品")
            team = FakeTeam()
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                project_dashboard.make_handler("http://127.0.0.1:1", contracts=store, team=team),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                body = json.dumps({"approved": True})
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    f"/api/contracts/{draft['contract_id']}/decision",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                self.assertEqual(team.calls, [])

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    f"/api/contracts/{draft['contract_id']}/decision",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-AI-Team-Action": "contract-decision",
                    },
                )
                response = conn.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["status"], "started")
                self.assertEqual(payload["run_id"], "run_ui_001")
                self.assertEqual(len(team.calls), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_build_state_degrades_when_bridge_is_unreachable(self):
        state = project_dashboard.build_state("http://127.0.0.1:1")
        self.assertEqual(state["agents"][0]["agent_id"], "bridge")
        self.assertFalse(state["agents"][0]["available"])


if __name__ == "__main__":
    unittest.main()
