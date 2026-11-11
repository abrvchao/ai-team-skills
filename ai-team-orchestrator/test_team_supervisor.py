from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from team_supervisor import TEAM_ORDER, TeamSupervisor


def valid_review(verdict: str = "accept") -> str:
    return (
        "## ACCEPTED_CHECKS\n- acceptance checked\n\n"
        "## FAILED_CHECKS\n- none\n\n"
        "## COUNTER_EVIDENCE\n- none\n\n"
        "## UNSUPPORTED_CLAIMS\n- none\n\n"
        "## REQUIRED_REPAIR\n- none\n\n"
        f"VERDICT: {verdict}\n"
    )


class FakeBridge:
    def __init__(self, *, extra_agents: list[str] | None = None) -> None:
        self._counter = 0
        self._tasks: dict[str, dict] = {}
        self.messages: list[tuple[str, str]] = []
        self.cancellations: list[str] = []
        self.unavailable: set[str] = set()
        # 原有 50 项历史测试模拟六 Worker 时代；新增角色需要用例显式开启。
        self.agent_ids = ["dsh", "gemini", "grok", "qwen", "muse", "doubao"]
        self.agent_ids += list(extra_agents or [])

    def agents(self):
        return [
            {
                "agent_id": agent_id,
                "configured": True,
                "available": agent_id not in self.unavailable,
                "queueable": agent_id not in self.unavailable,
            }
            for agent_id in self.agent_ids
        ]

    def submit(self, *, agent_id, title, prompt, workspace=".", metadata=None):
        self._counter += 1
        task_id = f"task_{self._counter}"
        task = {
            "task_id": task_id,
            "agent_id": agent_id,
            "title": title,
            "prompt": prompt,
            "workspace": workspace,
            "metadata": metadata or {},
            "status": "queued",
            "started": False,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        self._tasks[task_id] = task
        return dict(task)

    def task(self, task_id):
        return dict(self._tasks[task_id])

    def message(self, task_id: str, message: str):
        self.messages.append((task_id, message))
        return {"task_id": task_id, "message": message}

    def cancel(self, task_id: str):
        self.cancellations.append(task_id)
        task = self._tasks[task_id]
        task["status"] = "cancelled"
        task["finished_at"] = "2026-01-01T00:00:02+00:00"
        return dict(task)

    def complete(self, task_id: str, filename: str, content: str) -> None:
        task = self._tasks[task_id]
        workspace = Path(task["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        task["status"] = "completed"
        task["started"] = True
        task["started_at"] = "2026-01-01T00:00:00+00:00"
        task["finished_at"] = "2026-01-01T00:00:01+00:00"

    def fail(self, task_id: str, error: str, *, filename: str | None = None, content: str = "") -> None:
        task = self._tasks[task_id]
        if filename:
            workspace = Path(task["workspace"])
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / filename).write_text(content, encoding="utf-8")
        task["status"] = "failed"
        task["started"] = True
        task["started_at"] = "2026-01-01T00:00:00+00:00"
        task["finished_at"] = "2026-01-01T00:00:01+00:00"
        task["error"] = error


class TeamSupervisorTests(unittest.TestCase):
    def test_four_workers_are_assignable_with_research_only_mimo_boundary(self):
        # 新版多 Worker 能被明确选择；MiMo 不得被选择执行代码变更或测试验证。
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge(extra_agents=["mimo", "workbuddy"])
            bridge.unavailable.update({"gemini", "grok", "muse", "doubao"})
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            preview = supervisor.plan_run(
                goal="Radar V2 delivery feasibility with four online workers",
                participants=["dsh", "qwen", "mimo", "workbuddy"],
                implementation_enabled=False,
            )
            self.assertEqual(preview["participants"], ["dsh", "qwen", "mimo", "workbuddy"])
            self.assertEqual(
                {r["role_id"] for r in preview["roles"]},
                {"engineering_verifier", "implementation_architect",
                 "independent_research_reviewer", "implementation_quality_engineer"},
            )
            run = supervisor.start_run(
                goal="Radar V2 four-worker planning-only test",
                workspace=str(Path(tmp) / "work"),
                participants=preview["participants"],
                implementation_enabled=False,
            )
            self.assertEqual(len(run["primary"]), 4)
            self.assertEqual({task["agent_id"] for task in bridge._tasks.values()},
                             {"dsh", "qwen", "mimo", "workbuddy"})
            self.assertNotEqual(supervisor._select_implementation_worker(run), "mimo")
            self.assertNotEqual(supervisor._select_validation_worker(run, "dsh"), "mimo")

    def test_validation_verdict_accepts_markdown_wrapper_but_not_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "validation.md"
            report.write_text("## RESULT\n\n**VERDICT: accept**\n", encoding="utf-8")
            self.assertEqual(TeamSupervisor._validation_verdict(report), "accept")
            report.write_text("## RESULT\n\nVERDICT: PASS\n", encoding="utf-8")
            self.assertEqual(TeamSupervisor._validation_verdict(report), "accept")
            report.write_text("my verdict: accept\n", encoding="utf-8")
            self.assertIsNone(TeamSupervisor._validation_verdict(report))

    def test_validation_error_resolution_clears_obsolete_repair_worker_block(self):
        run = {
            "errors": [
                "implementation validation verdict: repair",
                "no healthy repair worker available",
            ]
        }
        TeamSupervisor._resolve_validation_errors(run)
        self.assertEqual(run["errors"], [])
        self.assertEqual(len(run["resolved_errors"]), 1)

    def test_full_team_primary_review_synthesis_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(
                bridge,
                state_dir=Path(tmp) / "state",
            )
            run = supervisor.start_run(
                goal="Design a tiny multi-agent validation protocol.",
                workspace=str(Path(tmp) / "work"),
            )

            self.assertEqual(run["participants"], bridge.agent_ids)
            self.assertEqual(run["status"], "primary_running")
            self.assertEqual(run["progress"]["version"], "run-progress-v0.1")
            self.assertEqual(run["progress"]["current_phase"], "primary")
            self.assertEqual(run["progress"]["phases"]["primary"]["task_count"], 6)
            self.assertTrue((Path(run["run_root"]) / "progress.json").is_file())
            self.assertTrue((Path(run["run_root"]) / "delivery_plan.json").is_file())
            self.assertEqual(run["delivery_plan"]["version"], "delivery-plan-v0.1")
            self.assertEqual(run["delivery_plan"]["phases"][0]["phase"], "primary")
            self.assertEqual(run["delivery_plan"]["phases"][-1]["phase"], "synthesis")
            self.assertEqual(run["progress"]["schedule"]["state"], "on_track")
            self.assertTrue(run["progress"]["schedule"]["project_due_at"])
            self.assertEqual(run["product_contract"]["version"], "product-task-v0.2")
            first_primary = run["primary"]["dsh"]
            first_meta = bridge._tasks[first_primary["task_id"]]["metadata"]
            self.assertEqual(first_meta["task_contract"]["role_id"], "engineering_verifier")
            self.assertEqual(first_meta["execution_budget"]["version"], "execution-budget-v0.1")
            self.assertEqual(first_meta["execution_budget"]["exploration"]["timeout_seconds"], 120)
            self.assertEqual(first_meta["execution_budget"]["exploration"]["max_tool_calls"], 28)
            self.assertEqual(first_meta["execution_budget"]["finalization"]["timeout_seconds"], 120)
            self.assertEqual(first_meta["execution_budget"]["finalization"]["max_tool_calls"], 0)
            self.assertFalse(first_meta["execution_budget"]["finalization"]["allow_new_exploration"])
            self.assertIn("Acceptance checks:", bridge._tasks[first_primary["task_id"]]["prompt"])
            self.assertEqual(len(run["primary"]), 6)
            primary_workspaces = {
                entry["workspace"] for entry in run["primary"].values()
            }
            self.assertEqual(len(primary_workspaces), 6)

            for agent_id, entry in run["primary"].items():
                bridge.complete(
                    entry["task_id"],
                    "primary.md",
                    f"# {agent_id}\nprimary result\n",
                )

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "review_running")
            self.assertEqual(run["progress"]["phases"]["primary"]["percent"], 100)
            self.assertEqual(run["progress"]["current_phase"], "review")
            self.assertEqual(len(run["reviews"]), 6)
            pairs = {
                (entry["reviewer"], entry["target"])
                for entry in run["reviews"].values()
            }
            self.assertEqual(
                pairs,
                {
                    ("dsh", "doubao"),
                    ("gemini", "dsh"),
                    ("grok", "gemini"),
                    ("qwen", "grok"),
                    ("muse", "qwen"),
                    ("doubao", "muse"),
                },
            )
            for entry in run["reviews"].values():
                review_meta = bridge._tasks[entry["task_id"]]["metadata"]
                self.assertEqual(review_meta["execution_budget"]["phase"], "review")
                self.assertEqual(review_meta["execution_budget"]["finalization"]["max_tool_calls"], 0)
                self.assertIn("primary result", review_meta["finalization_context"])
                review_prompt = bridge._tasks[entry["task_id"]]["prompt"]
                self.assertIn("Authoritative target excerpt", review_prompt)
                self.assertIn("primary result", review_prompt)
                inputs = Path(entry["workspace"]) / "inputs"
                self.assertEqual(len(list(inputs.glob("primary-*.md"))), 6)
                bridge.complete(
                    entry["task_id"],
                    "review.md",
                    valid_review(),
                )

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "synthesis_running")
            self.assertTrue(all(entry["review_contract_valid"] for entry in run["reviews"].values()))
            self.assertEqual(run["synthesis"]["agent_id"], "gemini")
            synthesis_meta = bridge._tasks[run["synthesis"]["task_id"]]["metadata"]
            self.assertEqual(synthesis_meta["execution_budget"]["phase"], "synthesis")
            self.assertIn("SYNTHESIS STAGED EVIDENCE", synthesis_meta["finalization_context"])
            self.assertIn("primary result", synthesis_meta["finalization_context"])
            self.assertIn("## ACCEPTED_CHECKS", synthesis_meta["finalization_context"])
            self.assertLessEqual(len(synthesis_meta["finalization_context"]), 62000)
            synthesis_prompt = bridge._tasks[run["synthesis"]["task_id"]]["prompt"]
            self.assertIn("Bounded authoritative evidence excerpt", synthesis_prompt)
            self.assertIn("SYNTHESIS STAGED EVIDENCE", synthesis_prompt)
            self.assertIn("primary result", synthesis_prompt)
            synth_inputs = Path(run["synthesis"]["workspace"]) / "inputs"
            self.assertEqual(len(list(synth_inputs.glob("primary-*.md"))), 6)
            self.assertEqual(len(list(synth_inputs.glob("review-*.md"))), 6)

            bridge.complete(
                run["synthesis"]["task_id"],
                "final.md",
                "# Final\nvalidated synthesis\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["progress"]["overall_percent"], 100)
            self.assertTrue(all(row["percent"] == 100 for row in run["progress"]["phases"].values()))
            self.assertTrue(Path(run["final_artifact"]).is_file())
            self.assertEqual(
                Path(run["final_artifact"]).read_text(encoding="utf-8"),
                "# Final\nvalidated synthesis\n",
            )

    def test_failed_run_progress_does_not_mark_unreached_phases_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Expose blocked progress truthfully.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
                implementation_enabled=True,
            )
            bridge.unavailable.update({"dsh", "gemini", "grok", "qwen", "doubao", "muse"})
            bridge.fail(run["primary"]["dsh"]["task_id"], "runtime failure")
            bridge.fail(run["primary"]["gemini"]["task_id"], "model unavailable")
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["progress"]["phases"]["primary"]["percent"], 100)
            self.assertEqual(run["progress"]["phases"]["review"]["percent"], 0)
            self.assertEqual(run["progress"]["phases"]["implementation"]["percent"], 0)
            self.assertEqual(run["progress"]["phases"]["validation"]["percent"], 0)
            self.assertEqual(run["progress"]["phases"]["review"]["state"], "blocked")

    def test_start_run_captures_dirty_workspace_baseline_before_run_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "AI Team Test"], cwd=repo, check=True)
            tracked = repo / "app.py"
            tracked.write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)

            # 中文说明：这些改动在 run 启动前已经存在，必须被记录为基线而不是本次 AI 改动。
            tracked.write_text("VALUE = 2\n", encoding="utf-8")
            untracked = repo / "notes.txt"
            untracked.write_text("pre-existing note\n", encoding="utf-8")

            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Capture the exact dirty-worktree baseline before doing product work.",
                workspace=str(repo),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
            )

            baseline = run["workspace_baseline"]
            self.assertEqual(baseline["version"], "workspace-baseline-v0.1")
            self.assertTrue(baseline["git"]["is_repo"])
            self.assertTrue(baseline["git"]["dirty"])
            self.assertTrue(Path(baseline["metadata_file"]).is_file())
            patch = Path(baseline["artifacts"]["tracked_patch"]).read_text(encoding="utf-8")
            self.assertIn("+VALUE = 2", patch)
            status = Path(baseline["artifacts"]["status"]).read_text(encoding="utf-8")
            self.assertIn("app.py", status)
            self.assertIn("notes.txt", status)
            self.assertNotIn(".ai-team-runs", status)
            manifest = json.loads(Path(baseline["artifacts"]["untracked_manifest"]).read_text(encoding="utf-8"))
            note = next(item for item in manifest if item["path"].endswith("notes.txt"))
            self.assertEqual(note["size_bytes"], len("pre-existing note\n".encode()))
            self.assertEqual(len(note["sha256"]), 64)

    def test_execution_budget_aligns_with_supervisor_deadlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Keep worker budgets consistent with supervisor deadlines.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "qwen"],
            )
            budget = bridge._tasks[run["primary"]["dsh"]["task_id"]]["metadata"]["execution_budget"]
            self.assertEqual(budget["supervisor_soft_deadline_seconds"], 90)
            self.assertEqual(budget["supervisor_hard_deadline_seconds"], 420)
            self.assertGreater(budget["exploration"]["timeout_seconds"], 90)
            self.assertLess(budget["exploration"]["timeout_seconds"], 420)
            self.assertEqual(budget["finalization"]["max_tool_calls"], 0)
            self.assertLessEqual(
                budget["exploration"]["timeout_seconds"] + budget["finalization"]["timeout_seconds"],
                420,
            )
            synthesis_budget = supervisor._execution_budget(run, "synthesis")
            self.assertEqual(synthesis_budget["exploration"]["max_tool_calls"], 20)
            self.assertEqual(synthesis_budget["finalization"]["max_tool_calls"], 0)

    def test_requested_subset_preserves_team_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Compare two views.",
                workspace=str(Path(tmp) / "work"),
                participants=["doubao", "dsh", "grok"],
            )
            self.assertEqual(run["participants"], ["dsh", "grok", "doubao"])

    def test_missing_primary_artifacts_fail_truthfully(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Fail when a claimed artifact is absent.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
            )
            for entry in run["primary"].values():
                task = bridge._tasks[entry["task_id"]]
                task["status"] = "completed"
                task["started"] = True
                task["finished_at"] = "2026-01-01T00:00:01+00:00"

            with self.assertRaises(FileNotFoundError):
                supervisor.advance(run["run_id"])

    def test_phase_deadline_nudges_then_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Exercise deadline policy.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok"],
            )
            for agent_id in ("dsh", "gemini"):
                entry = run["primary"][agent_id]
                bridge.complete(entry["task_id"], "primary.md", f"# {agent_id}\n")
            run["phase_started_at"] = "2020-01-01T00:00:00+00:00"
            run["policy"]["primary_soft_timeout_seconds"] = 1
            run["policy"]["primary_hard_timeout_seconds"] = 2
            run["policy"]["min_primary_success"] = 2
            supervisor.runs.save(run)

            advanced = supervisor.advance(run["run_id"])
            self.assertIn("primary", advanced["nudges"])
            self.assertEqual(len(bridge.messages), 1)
            self.assertIn("Converge now", bridge.messages[0][1])
            self.assertEqual(len(bridge.cancellations), 1)
            self.assertEqual(advanced["status"], "primary_running")
            self.assertEqual(advanced["primary"]["grok"]["reassignment_count"], 1)
            self.assertEqual(advanced["primary"]["grok"]["fallback_reason"], "timeout")

    def test_failed_primary_with_artifact_is_salvaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Keep valid work even when a worker process fails.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
            )
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# dsh\n")
            bridge.fail(
                run["primary"]["gemini"]["task_id"],
                "runtime crashed after artifact write",
                filename="primary.md",
                content="# gemini partial but valid\n",
            )

            advanced = supervisor.advance(run["run_id"])
            self.assertEqual(advanced["status"], "review_running")
            self.assertTrue(advanced["primary"]["gemini"]["salvaged"])
            self.assertTrue(Path(advanced["primary"]["gemini"]["artifact"]).is_file())
            self.assertTrue(any("salvaged primary.md" in item for item in advanced["warnings"]))

    def test_degraded_synthesis_records_missing_primary_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Synthesize useful evidence even when most workers fail.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok"],
            )
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# dsh\n")
            bridge.fail(run["primary"]["gemini"]["task_id"], "model unavailable")
            bridge.fail(run["primary"]["grok"]["task_id"], "free Grok Build usage limit")
            bridge.unavailable.update({"gemini", "grok", "qwen", "doubao", "muse"})

            advanced = supervisor.advance(run["run_id"])
            self.assertEqual(advanced["status"], "synthesis_running")
            self.assertTrue(advanced["degraded"])
            self.assertEqual(
                advanced["coverage_gaps"]["missing_primary_roles"],
                ["deep_researcher", "trend_scout_red_team"],
            )
            self.assertEqual(advanced["primary"]["gemini"]["failure_class"], "model_unavailable")
            self.assertEqual(advanced["primary"]["grok"]["failure_class"], "quota_exhausted")
            prompt = bridge._tasks[advanced["synthesis"]["task_id"]]["prompt"]
            self.assertIn("Run degraded: True", prompt)
            self.assertIn("missing_primary_roles", prompt)


    def test_review_structure_gate_excludes_malformed_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Require deterministic review structure.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
                acceptance=["Primary recommendations must identify evidence gaps."],
                evidence_required=["Cite workspace or external evidence for material claims."],
                stop_conditions=["Stop after acceptance checks can be evaluated."],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "review_running")
            review_entries = list(run["reviews"].values())
            bridge.complete(review_entries[0]["task_id"], "review.md", valid_review())
            bridge.complete(review_entries[1]["task_id"], "review.md", "# looks like a review but has no contract\n")

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "synthesis_running")
            self.assertTrue(run["degraded"])
            valid_count = sum(1 for item in run["reviews"].values() if item.get("review_contract_valid"))
            self.assertEqual(valid_count, 1)
            self.assertEqual(run["coverage_gaps"]["missing_review_count"], 1)
            self.assertTrue(any("structure gate" in warning for warning in run["warnings"]))
            synth_inputs = Path(run["synthesis"]["workspace"]) / "inputs"
            self.assertEqual(len(list(synth_inputs.glob("review-*.md"))), 1)

    def test_grok_quota_failure_reassigns_red_team_role_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Exercise role fallback after provider quota exhaustion.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok"],
            )
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# engineering\n")
            bridge.complete(run["primary"]["gemini"]["task_id"], "primary.md", "# research\n")
            original_grok_task = run["primary"]["grok"]["task_id"]
            bridge.fail(original_grok_task, "grok_quota_exhausted: free Grok Build usage limit")

            advanced = supervisor.advance(run["run_id"])
            red_team = advanced["primary"]["grok"]
            self.assertEqual(advanced["status"], "primary_running")
            self.assertEqual(red_team["role_id"], "trend_scout_red_team")
            self.assertEqual(red_team["original_worker"], "grok")
            self.assertEqual(advanced["roles"]["trend_scout_red_team"]["preferred_worker"], "grok")
            self.assertEqual(advanced["roles"]["trend_scout_red_team"]["current_worker"], "gemini")
            self.assertEqual(red_team["worker_id"], "gemini")
            self.assertEqual(red_team["fallback_worker"], "gemini")
            self.assertEqual(red_team["fallback_from_task"], original_grok_task)
            self.assertEqual(red_team["fallback_reason"], "quota_exhausted")
            self.assertEqual(red_team["reassignment_count"], 1)
            self.assertEqual(len(red_team["attempts"]), 2)
            self.assertEqual(red_team["attempts"][0]["status"], "failed")
            self.assertEqual(red_team["attempts"][0]["failure_class"], "quota_exhausted")
            self.assertEqual(advanced["reassignments"][0]["role_id"], "trend_scout_red_team")

            fallback_task = red_team["task_id"]
            self.assertNotEqual(fallback_task, original_grok_task)
            fallback_meta = bridge._tasks[fallback_task]["metadata"]
            self.assertEqual(fallback_meta["team_role"], "trend_scout_red_team")
            self.assertEqual(fallback_meta["preferred_worker"], "grok")
            self.assertEqual(fallback_meta["current_worker"], "gemini")
            self.assertEqual(fallback_meta["fallback_reason"], "quota_exhausted")
            self.assertIn("Stable role id: trend_scout_red_team", bridge._tasks[fallback_task]["prompt"])

            bridge.complete(fallback_task, "primary.md", "# red team via fallback\n")
            reviewed = supervisor.advance(run["run_id"])
            self.assertEqual(reviewed["status"], "review_running")
            self.assertEqual(reviewed["primary"]["grok"]["worker_id"], "gemini")
            self.assertTrue(all(
                item["reviewer"] != item["target_worker"]
                for item in reviewed["reviews"].values()
            ))
            staged = next(iter(reviewed["reviews"].values()))
            names = {path.name for path in (Path(staged["workspace"]) / "inputs").glob("primary-*.md")}
            self.assertIn("primary-trend_scout_red_team.md", names)

    def test_fallback_policy_without_reassign_degrades_instead_of_spending_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Make a bounded stop-loss decision without paying for redundant fallback work.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "grok", "qwen"],
                fallback_policy="salvage|degrade",
            )
            original_grok_task = run["primary"]["grok"]["task_id"]
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# dsh\n")
            bridge.complete(run["primary"]["qwen"]["task_id"], "primary.md", "# qwen\n")
            bridge.fail(original_grok_task, "quota exhausted")

            advanced = supervisor.advance(run["run_id"])

            self.assertEqual(advanced["primary"]["grok"]["task_id"], original_grok_task)
            self.assertEqual(advanced["primary"]["grok"].get("reassignment_count", 0), 0)
            self.assertIn(
                "trend_scout_red_team",
                (advanced.get("coverage_gaps") or {}).get("missing_primary_roles", []),
            )
            self.assertTrue(
                any(
                    "reassignment skipped by fallback policy" in item
                    for item in advanced.get("warnings", [])
                )
            )
            self.assertIn(advanced["phase"], {"review", "synthesis"})

    def test_salvaged_primary_prevents_role_reassignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Prefer produced evidence over unnecessary fallback.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "grok"],
            )
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# engineering\n")
            original_task = run["primary"]["grok"]["task_id"]
            bridge.fail(
                original_task,
                "grok_quota_exhausted",
                filename="primary.md",
                content="# usable red-team artifact\n",
            )

            advanced = supervisor.advance(run["run_id"])
            entry = advanced["primary"]["grok"]
            self.assertTrue(entry["salvaged"])
            self.assertEqual(entry["reassignment_count"], 0)
            self.assertEqual(entry["task_id"], original_task)
            self.assertNotIn("reassignments", advanced)
            self.assertEqual(advanced["status"], "review_running")

    def test_late_original_artifact_is_salvaged_after_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Preserve late evidence from a superseded attempt.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok"],
            )
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# engineering\n")
            bridge.complete(run["primary"]["grok"]["task_id"], "primary.md", "# red team\n")
            original_task = run["primary"]["gemini"]["task_id"]
            original_workspace = Path(run["primary"]["gemini"]["workspace"])
            bridge.fail(original_task, "model unavailable")

            run = supervisor.advance(run["run_id"])
            fallback_task = run["primary"]["gemini"]["task_id"]
            self.assertNotEqual(fallback_task, original_task)

            original_workspace.mkdir(parents=True, exist_ok=True)
            late = original_workspace / "primary.md"
            late.write_text("# late original research\n", encoding="utf-8")
            bridge.fail(fallback_task, "runtime crashed")

            run = supervisor.advance(run["run_id"])
            entry = run["primary"]["gemini"]
            self.assertTrue(entry["salvaged"])
            self.assertEqual(entry["salvaged_from_attempt"], 1)
            self.assertEqual(entry["salvaged_from_worker"], "gemini")
            self.assertEqual(Path(entry["artifact"]), late)
            self.assertTrue(any(item["path"] == str(late) for item in entry["attempt_artifacts"]))
            self.assertEqual(run["status"], "review_running")

    def test_fallback_excludes_workers_that_failed_in_same_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Do not send fallback work to a worker that already failed this run.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "qwen", "doubao"],
            )
            bridge.complete(run["primary"]["gemini"]["task_id"], "primary.md", "# research\n")
            bridge.fail(run["primary"]["dsh"]["task_id"], "DSH timed out after 60000ms")
            bridge.fail(run["primary"]["qwen"]["task_id"], "Qwen timed out after 60000ms")
            bridge.fail(run["primary"]["doubao"]["task_id"], "runtime failure")

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["primary"]["dsh"]["worker_id"], "gemini")
            self.assertTrue(all(
                item["fallback_worker"] not in {"dsh", "qwen", "doubao"}
                for item in run["reassignments"]
            ))
            self.assertEqual(len(run["reassignments"]), 3)
            plan = run["fallback_plans"][0]
            self.assertEqual(plan["status"], "submitted")
            self.assertEqual(len(plan["assignments"]), 3)
            self.assertEqual(
                len({item["assigned_worker"] for item in plan["assignments"]}),
                3,
            )
            self.assertEqual(
                {
                    item["role_owner"]: item["assigned_worker"]
                    for item in plan["assignments"]
                },
                {
                    "dsh": "gemini",
                    "qwen": "muse",
                    "doubao": "grok",
                },
            )
            self.assertTrue(all(
                item["fallback_plan_id"] == plan["plan_id"]
                for item in run["reassignments"]
            ))
            self.assertEqual(
                plan["health_snapshot"]["dsh"]["run_local_health"],
                "unhealthy",
            )
            self.assertIsNotNone(plan["objective"])

    def test_batch_fallback_plan_records_unassigned_roles_when_no_healthy_worker_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Record an auditable batch plan even when no fallback is possible.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
            )
            # 任务启动后模拟所有其他 Worker 全局不可用，两个当前 Worker 也在本轮失败。
            bridge.unavailable.update(TEAM_ORDER)
            bridge.fail(
                run["primary"]["dsh"]["task_id"],
                "runtime failure",
            )
            bridge.fail(
                run["primary"]["gemini"]["task_id"],
                "model unavailable",
            )

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "failed")
            plan = run["fallback_plans"][0]
            self.assertEqual(plan["status"], "unassigned")
            self.assertEqual(plan["assignments"], [])
            self.assertEqual(
                {item["role_id"] for item in plan["unassigned"]},
                {"engineering_verifier", "deep_researcher"},
            )
            self.assertTrue(all(
                item["reason"] == "no_healthy_untried_fallback"
                for item in plan["unassigned"]
            ))

    def test_worker_health_exposes_global_and_run_local_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Expose global and run-local worker health separately.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok"],
            )
            bridge.fail(
                run["primary"]["dsh"]["task_id"],
                "DSH timed out after 60000ms",
            )
            bridge.complete(
                run["primary"]["gemini"]["task_id"],
                "primary.md",
                "# research\n",
            )
            bridge.fail(
                run["primary"]["grok"]["task_id"],
                "grok_quota_exhausted",
                filename="primary.md",
                content="# usable red-team artifact\n",
            )

            # 这里只刷新状态，不推进 fallback，便于直接观察健康模型。
            run = supervisor.get_run(run["run_id"], refresh=True)
            health = run["worker_health"]

            self.assertEqual(health["dsh"]["global_status"], "available")
            self.assertEqual(health["dsh"]["run_local_health"], "unhealthy")
            self.assertFalse(health["dsh"]["eligible_for_new_assignment"])
            self.assertIn("timeout", health["dsh"]["failure_classes"])

            self.assertEqual(health["gemini"]["run_local_health"], "healthy")
            self.assertTrue(health["gemini"]["eligible_for_new_assignment"])
            self.assertIn("deep_researcher", health["gemini"]["roles_completed"])

            self.assertEqual(health["grok"]["run_local_health"], "degraded")
            self.assertFalse(health["grok"]["eligible_for_new_assignment"])
            self.assertIn("trend_scout_red_team", health["grok"]["roles_completed"])

            # 进度快照与 run 顶层必须引用同一套健康真相。
            self.assertEqual(
                run["progress"]["worker_health"]["dsh"]["run_local_health"],
                "unhealthy",
            )

    def test_unavailable_queued_primary_is_cancelled_and_reassigned_without_hard_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Reassign a worker that disappears before leasing its task.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
            )
            original_task = run["primary"]["gemini"]["task_id"]
            # 模拟任务已分配，但 Gemini 在 lease（领取）前变成全局不可用。
            bridge.unavailable.add("gemini")
            run["policy"]["unavailable_reassign_grace_seconds"] = 0
            supervisor.runs.save(run)

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "primary_running")
            self.assertIn(original_task, bridge.cancellations)
            reassigned = run["primary"]["gemini"]
            self.assertEqual(reassigned["reassignment_count"], 1)
            self.assertNotEqual(reassigned["worker_id"], "gemini")
            self.assertEqual(
                reassigned["attempts"][0]["failure_class"],
                "model_unavailable",
            )
            self.assertTrue(any(
                "globally unavailable queued primary" in item
                for item in run["warnings"]
            ))
            self.assertEqual(run["fallback_plans"][-1]["status"], "submitted")

    def test_degraded_primary_worker_is_not_reused_as_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Do not reuse a degraded producer as a reviewer.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "qwen"],
            )
            bridge.complete(
                run["primary"]["dsh"]["task_id"],
                "primary.md",
                "# engineering\n",
            )
            bridge.fail(
                run["primary"]["qwen"]["task_id"],
                "runtime failure",
                filename="primary.md",
                content="# architecture with usable evidence\n",
            )

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "review_running")
            self.assertEqual(
                run["worker_health"]["qwen"]["run_local_health"],
                "degraded",
            )
            self.assertFalse(
                run["worker_health"]["qwen"]["eligible_for_new_assignment"]
            )
            self.assertNotIn(
                "qwen",
                {entry["reviewer"] for entry in run["reviews"].values()},
            )

    def test_zero_independent_reviewers_degrades_without_review_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Continue truthfully when no independent reviewer remains.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "qwen"],
            )
            bridge.complete(
                run["primary"]["dsh"]["task_id"],
                "primary.md",
                "# engineering\n",
            )
            bridge.fail(
                run["primary"]["qwen"]["task_id"],
                "Qwen timed out after 60000ms",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["primary"]["qwen"]["worker_id"], "dsh")
            bridge.complete(
                run["primary"]["qwen"]["task_id"],
                "primary.md",
                "# architecture by DSH fallback\n",
            )

            # 只剩 DSH 健康；它又是两个成果的生产者，因此不能自己审自己。
            bridge.unavailable.update({"gemini", "grok", "qwen", "muse", "doubao"})
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "synthesis_running")
            self.assertEqual(run["reviews"], {})
            self.assertTrue(run["degraded"])
            self.assertEqual(run["coverage_gaps"]["missing_review_count"], 2)
            self.assertTrue(any(
                "no healthy independent reviewer" in item
                for item in run["warnings"]
            ))

    def test_stop_loss_policy_can_start_with_one_worker_but_marks_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            bridge.unavailable.update({"dsh", "gemini", "grok", "muse", "doubao"})
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")

            run = supervisor.start_run(
                goal="Continue a bounded Radar handoff with one healthy worker.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "qwen"],
                fallback_policy="salvage|degrade",
                implementation_enabled=True,
            )

            self.assertEqual(run["participants"], ["qwen"])
            self.assertTrue(run["degraded"])
            self.assertTrue(any(
                "single-worker degraded start" in item
                for item in run.get("warnings", [])
            ))

            bridge.complete(
                run["primary"]["qwen"]["task_id"],
                "primary.md",
                "# bounded primary result\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "synthesis_running")
            self.assertEqual(run["reviews"], {})
            self.assertEqual(run["coverage_gaps"]["missing_review_count"], 1)

    def test_default_policy_still_requires_two_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            bridge.unavailable.update({"dsh", "gemini", "grok", "muse", "doubao"})
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            with self.assertRaises(RuntimeError):
                supervisor.start_run(
                    goal="Normal delivery still needs two workers.",
                    workspace=str(Path(tmp) / "work"),
                    participants=["dsh", "qwen"],
                )

    def test_validation_selector_skips_worker_that_failed_earlier_in_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Do not select an unhealthy worker for independent validation.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "qwen"],
            )
            bridge.fail(
                run["primary"]["qwen"]["task_id"],
                "Qwen timed out after 60000ms",
            )
            bridge.complete(
                run["primary"]["dsh"]["task_id"],
                "primary.md",
                "# engineering\n",
            )
            bridge.complete(
                run["primary"]["gemini"]["task_id"],
                "primary.md",
                "# research\n",
            )
            run = supervisor.get_run(run["run_id"], refresh=True)

            # Qwen 在验证偏好里排第一，但本轮已 timeout，所以必须跳过。
            reviewer = supervisor._select_validation_worker(run, "dsh")
            self.assertEqual(reviewer, "gemini")

    def test_fallback_prefers_lower_loaded_healthy_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Spread fallback roles across healthy candidates when possible.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok", "qwen", "doubao"],
            )
            bridge.complete(run["primary"]["dsh"]["task_id"], "primary.md", "# dsh\n")
            bridge.complete(run["primary"]["qwen"]["task_id"], "primary.md", "# qwen\n")
            bridge.complete(run["primary"]["doubao"]["task_id"], "primary.md", "# doubao\n")
            bridge.fail(run["primary"]["grok"]["task_id"], "grok_quota_exhausted")
            bridge.complete(run["primary"]["gemini"]["task_id"], "primary.md", "# gemini\n")

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["primary"]["grok"]["worker_id"], "gemini")
            self.assertEqual(run["reassignments"][0]["fallback_worker"], "gemini")

    def test_salvage_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Do not report the same salvage twice.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini"],
            )
            entry = run["primary"]["gemini"]
            bridge.fail(entry["task_id"], "runtime failure", filename="primary.md", content="# artifact\n")
            supervisor._refresh_entry(entry)
            self.assertTrue(supervisor._salvage_entry(entry, "primary.md"))
            self.assertFalse(supervisor._salvage_entry(entry, "primary.md"))


    def test_implementation_gate_runs_real_workspace_then_independent_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            product_workspace = Path(tmp) / "product"
            run = supervisor.start_run(
                goal="Validate an end-to-end product execution gate.",
                workspace=str(product_workspace),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
                acceptance=["Implementation evidence must be independently validated."],
                deliverable_artifacts=["DELIVERABLE.md"],
            )

            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\nImplement the accepted change.\n")

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "implementation_running")
            implementation = run["implementation"]
            self.assertEqual(Path(implementation["workspace"]).resolve(), product_workspace.resolve())
            self.assertEqual(implementation["agent_id"], "dsh")
            implementation_meta = bridge._tasks[implementation["task_id"]]["metadata"]
            self.assertEqual(implementation_meta["execution_budget"]["phase"], "implementation")
            self.assertEqual(implementation_meta["execution_budget"]["exploration"]["max_tool_calls"], 40)
            self.assertIn("Never commit or push", bridge._tasks[implementation["task_id"]]["prompt"])
            self.assertIn("DELIVERABLE.md", bridge._tasks[implementation["task_id"]]["prompt"])
            self.assertIn("DELIVERABLE.md", implementation_meta["expected_artifacts"])
            self.assertIn("implementation_report.md", implementation["artifact_relative"])

            (product_workspace / "DELIVERABLE.md").write_text(
                "AI_TEAM_ARTIFACT_STATUS: completed\n# Delivered\n",
                encoding="utf-8",
            )
            bridge.complete(
                implementation["task_id"],
                implementation["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n## CHANGED_FILES\n- discovery.py\n## ACCEPTANCE_EVIDENCE\n- ok\n## TEST_RESULTS\n- pass\n## UNRESOLVED\n- none\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "validation_running")
            validation = run["validation"]
            validation_meta = bridge._tasks[validation["task_id"]]["metadata"]
            self.assertEqual(validation_meta["execution_budget"]["phase"], "validation")
            self.assertEqual(validation_meta["execution_budget"]["exploration"]["max_tool_calls"], 32)
            self.assertEqual(validation_meta["execution_budget"]["finalization"]["max_tool_calls"], 0)
            self.assertIn("DELIVERABLE.md", bridge._tasks[validation["task_id"]]["prompt"])
            self.assertNotEqual(validation["agent_id"], implementation["agent_id"])
            self.assertEqual(Path(validation["workspace"]).resolve(), product_workspace.resolve())

            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- pass\n## FAILED_CHECKS\n- none\n## DIFF_SCOPE\n- scoped\n## TEST_EVIDENCE\n- independently passed\n## REQUIRED_REPAIR\n- none\nVERDICT: accept\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["phase"], "completed")
            self.assertEqual(run["implementation_validation_verdict"], "accept")
            self.assertTrue(Path(run["implementation"]["artifact"]).is_file())
            self.assertTrue(Path(run["validation"]["artifact"]).is_file())

    def test_missing_mandatory_product_deliverable_fails_before_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Require the actual product deliverable before validation.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
                deliverable_artifacts=["MUST_EXIST.md"],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            implementation = run["implementation"]
            bridge.complete(
                implementation["task_id"],
                implementation["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n## CHANGED_FILES\n- none\n## ACCEPTANCE_EVIDENCE\n- claimed\n## TEST_RESULTS\n- none\n## UNRESOLVED\n- none\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["phase"], "failed")
            self.assertIsNone(run.get("validation"))
            self.assertTrue(
                any("MUST_EXIST.md" in item for item in run.get("errors", []))
            )

    def test_validation_repair_starts_bounded_repair_then_independent_revalidation_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            product_workspace = Path(tmp) / "product"
            run = supervisor.start_run(
                goal="Reject an implementation that fails independent validation.",
                workspace=str(product_workspace),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
                acceptance=["Regression must be fixed without unrelated changes."],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            implementation = run["implementation"]
            bridge.complete(
                implementation["task_id"],
                implementation["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- partial\n## FAILED_CHECKS\n- regression\n## DIFF_SCOPE\n- too broad\n## TEST_EVIDENCE\n- failed\n## REQUIRED_REPAIR\n- narrow the change\nVERDICT: repair\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "repair_running")
            self.assertEqual(run["phase"], "repair")
            self.assertEqual(run["implementation_validation_verdict"], "repair")
            self.assertTrue(any("validation verdict: repair" in item for item in run["errors"]))
            repair = run["repair"]
            self.assertNotEqual(repair["agent_id"], validation["agent_id"])
            repair_task = bridge._tasks[repair["task_id"]]
            self.assertEqual(repair_task["metadata"]["team_phase"], "repair")
            self.assertEqual(repair_task["metadata"]["execution_budget"]["phase"], "repair")
            self.assertEqual(
                repair_task["metadata"]["acceptance"],
                ["Regression must be fixed without unrelated changes."],
            )
            self.assertIn("FAILED_CHECKS", repair_task["prompt"])
            self.assertIn("regression", repair_task["prompt"])
            self.assertIn("narrow the change", repair_task["prompt"])
            self.assertEqual(len(run["validation_history"]), 1)
            self.assertIsNone(run["validation"])

            bridge.complete(
                repair["task_id"],
                repair["artifact_relative"],
                "REPAIR_STATUS: changed\n"
                "## FAILED_CHECKS_ADDRESSED\n- regression\n"
                "## CHANGED_FILES\n- `fix.py`\n"
                "## ACCEPTANCE_EVIDENCE\n- fixed\n"
                "## TEST_RESULTS\n- `python3 -m unittest` passes\n"
                "## UNRESOLVED\n- none\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "validation_running")
            self.assertEqual(len(run["repairs"]), 1)
            self.assertIsNone(run["repair"])
            revalidation = run["validation"]
            self.assertNotEqual(revalidation["agent_id"], repair["agent_id"])
            self.assertEqual(revalidation["repair_attempt"], 1)
            self.assertEqual(
                Path(revalidation["change_report"]),
                Path(run["repairs"][0]["artifact"]),
            )

            bridge.complete(
                revalidation["task_id"],
                revalidation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- pass\n"
                "## FAILED_CHECKS\n- none\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- independently passed\n"
                "## REQUIRED_REPAIR\n- none\n"
                "VERDICT: accept\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["implementation_validation_verdict"], "accept")
            self.assertEqual(run["progress"]["phases"]["repair"]["percent"], 100)
            self.assertFalse(any("validation verdict: repair" in item for item in run["errors"]))

    def test_failed_repair_without_artifact_reassigns_to_another_healthy_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Recover when the first repair worker crashes before producing an artifact.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen", "grok"],
                implementation_enabled=True,
                acceptance=["The defect is fixed and independently validated."],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- partial\n"
                "## FAILED_CHECKS\n- regression\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- failed\n"
                "## REQUIRED_REPAIR\n- fix regression\n"
                "VERDICT: repair\n",
            )
            run = supervisor.advance(run["run_id"])
            first = dict(run["repair"])
            bridge.fail(first["task_id"], "repair worker crashed")

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "repair_running")
            self.assertEqual(len(run["repairs"]), 1)
            self.assertEqual(run["repairs"][0]["agent_id"], first["agent_id"])
            self.assertNotEqual(run["repair"]["agent_id"], first["agent_id"])
            self.assertEqual(run["repair"]["attempt"], 2)
            self.assertTrue(any(
                "failed without ready artifact; starting bounded fallback" in item
                for item in run["warnings"]
            ))
            self.assertFalse(any(
                "expected repair artifact missing or incomplete" in item
                for item in run["errors"]
            ))

    def test_repair_loop_stops_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Bound repeated repair attempts.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n",
            )
            run = supervisor.advance(run["run_id"])

            repair_verdict = (
                "## ACCEPTED_CHECKS\n- partial\n"
                "## FAILED_CHECKS\n- still failing\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- failed\n"
                "## REQUIRED_REPAIR\n- fix again\n"
                "VERDICT: repair\n"
            )
            repair_report = (
                "REPAIR_STATUS: changed\n"
                "## FAILED_CHECKS_ADDRESSED\n- attempted\n"
                "## CHANGED_FILES\n- `fix.py`\n"
                "## ACCEPTANCE_EVIDENCE\n- partial\n"
                "## TEST_RESULTS\n- still needs validation\n"
                "## UNRESOLVED\n- possible regression\n"
            )

            for expected_attempt in (1, 2):
                validation = run["validation"]
                bridge.complete(
                    validation["task_id"],
                    validation["artifact_relative"],
                    repair_verdict,
                )
                run = supervisor.advance(run["run_id"])
                self.assertEqual(run["status"], "repair_running")
                self.assertEqual(run["repair"]["attempt"], expected_attempt)
                bridge.complete(
                    run["repair"]["task_id"],
                    run["repair"]["artifact_relative"],
                    repair_report,
                )
                run = supervisor.advance(run["run_id"])
                self.assertEqual(run["status"], "validation_running")

            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                repair_verdict,
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["phase"], "failed")
            self.assertEqual(len(run["repairs"]), 2)
            self.assertTrue(any("repair attempts exhausted after 2" in item for item in run["errors"]))

    def test_blocked_repair_consumes_attempt_and_starts_bounded_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Retry a repair that stopped before it became safe.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen", "grok"],
                implementation_enabled=True,
                acceptance=["The defect is fixed and tests pass."],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: blocked\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- none\n"
                "## FAILED_CHECKS\n- code still broken\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- failing\n"
                "## REQUIRED_REPAIR\n- fix the call site and add tests\n"
                "VERDICT: repair\n",
            )
            run = supervisor.advance(run["run_id"])
            first = dict(run["repair"])
            self.assertEqual(first["attempt"], 1)
            bridge.complete(
                first["task_id"],
                first["artifact_relative"],
                "REPAIR_STATUS: blocked\n"
                "## FAILED_CHECKS_ADDRESSED\n- partial only\n"
                "## CHANGED_FILES\n- `fix.py` partially edited\n"
                "## ACCEPTANCE_EVIDENCE\n- not verified\n"
                "## TEST_RESULTS\n- not run\n"
                "## UNRESOLVED\n- call site still broken\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "repair_running")
            self.assertEqual(run["phase"], "repair")
            self.assertEqual(len(run["repairs"]), 1)
            self.assertEqual(run["repairs"][0]["repair_status"], "blocked")
            second = run["repair"]
            self.assertEqual(second["attempt"], 2)
            self.assertNotEqual(second["agent_id"], first["agent_id"])
            self.assertNotEqual(second["agent_id"], validation["agent_id"])
            self.assertEqual(Path(second["previous_change_report"]), Path(run["repairs"][0]["artifact"]))
            self.assertTrue(any("starting bounded retry" in item for item in run["warnings"]))

    def test_validation_blocked_stops_without_auto_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Stop truthfully when validation is blocked.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- unknown\n"
                "## FAILED_CHECKS\n- none proven\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- environment unavailable\n"
                "## REQUIRED_REPAIR\n- none until environment returns\n"
                "VERDICT: blocked\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "blocked")
            self.assertEqual(run["phase"], "blocked")
            self.assertIsNone(run["repair"])
            self.assertEqual(run["repairs"], [])

    def test_validation_blocked_with_actionable_required_repair_normalizes_to_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Repair a workspace-fixable validation failure.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
                acceptance=["Regression is fixed."],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: blocked\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- none\n"
                "## FAILED_CHECKS\n- product code still fails\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- missing regression\n"
                "## REQUIRED_REPAIR\n- update product code\n- add the missing regression test\n"
                "VERDICT: blocked\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "repair_running")
            self.assertEqual(run["phase"], "repair")
            self.assertEqual(run["implementation_validation_reported_verdict"], "blocked")
            self.assertEqual(run["implementation_validation_verdict"], "repair")
            self.assertEqual(run["validation_history"][0]["reported_verdict"], "blocked")
            self.assertEqual(run["validation_history"][0]["verdict"], "repair")
            self.assertTrue(any("normalized to repair" in item for item in run["warnings"]))

    def test_terminal_blocked_run_with_actionable_repair_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Recover an old misclassified validation block.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: blocked\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- none\n"
                "## FAILED_CHECKS\n- code not fixed\n"
                "## DIFF_SCOPE\n- scoped\n"
                "## TEST_EVIDENCE\n- failing\n"
                "## REQUIRED_REPAIR\n- fix code and rerun tests\n"
                "VERDICT: blocked\n",
            )
            supervisor._refresh_entry(validation)
            artifact = Path(validation["workspace"]) / validation["artifact_relative"]
            validation["artifact"] = str(artifact)
            validation["reported_verdict"] = "blocked"
            validation["verdict"] = "blocked"
            run["status"] = "blocked"
            run["phase"] = "blocked"
            run["implementation_validation_reported_verdict"] = "blocked"
            run["implementation_validation_verdict"] = "blocked"
            run.setdefault("errors", []).append("implementation validation verdict: blocked")
            supervisor.runs.save(run)

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "repair_running")
            self.assertEqual(run["phase"], "repair")
            self.assertEqual(run["implementation_validation_reported_verdict"], "blocked")
            self.assertEqual(run["implementation_validation_verdict"], "repair")
            self.assertTrue(any(
                item.get("resolution") == "repairable blocked verdict normalized to repair"
                for item in run.get("resolved_errors", [])
            ))

    def test_missing_independent_validator_blocks_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Block truthfully when only the implementation worker remains healthy.",
                workspace=str(Path(tmp) / "product"),
                participants=["dsh", "qwen"],
                implementation_enabled=True,
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            implementation = run["implementation"]
            self.assertEqual(implementation["agent_id"], "dsh")

            # 实现完成前让所有其他 Worker 全局不可用，模拟真实资源覆盖缺口。
            bridge.unavailable.update({"gemini", "grok", "qwen", "muse", "doubao"})
            bridge.complete(
                implementation["task_id"],
                implementation["artifact_relative"],
                "IMPLEMENTATION_STATUS: changed\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "blocked")
            self.assertEqual(run["phase"], "blocked")
            self.assertIsNone(run["validation"])
            self.assertTrue(
                run["coverage_gaps"]["missing_independent_validator"]
            )
            self.assertTrue(any(
                "no healthy independent worker" in item
                for item in run["errors"]
            ))

            # 外部容量恢复后必须只续跑 validation，不能重跑 Primary / Synthesis / Implementation。
            implementation_task_id = implementation["task_id"]
            primary_task_ids = {
                key: entry["task_id"]
                for key, entry in run["primary"].items()
            }
            synthesis_task_id = run["synthesis"]["task_id"]
            bridge.unavailable.discard("qwen")

            resumed = supervisor.advance(run["run_id"])

            self.assertEqual(resumed["status"], "validation_running")
            self.assertEqual(resumed["phase"], "validation")
            self.assertIsNotNone(resumed["validation"])
            self.assertNotEqual(
                resumed["validation"]["agent_id"],
                implementation["agent_id"],
            )
            self.assertEqual(
                resumed["implementation"]["task_id"],
                implementation_task_id,
            )
            self.assertEqual(
                {key: entry["task_id"] for key, entry in resumed["primary"].items()},
                primary_task_ids,
            )
            self.assertEqual(resumed["synthesis"]["task_id"], synthesis_task_id)
            self.assertFalse(
                (resumed.get("coverage_gaps") or {}).get(
                    "missing_independent_validator",
                    False,
                )
            )
            self.assertTrue(any(
                item.get("resolution")
                == "independent validator became available; blocked validation gate resumed"
                for item in resumed.get("resolved_errors", [])
            ))

    def test_in_progress_checkpoint_is_not_usable_or_salvageable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(goal="Checkpoint truth.", workspace=str(Path(tmp) / "work"), participants=["dsh", "qwen"])
            entry = run["primary"]["dsh"]
            workspace = Path(entry["workspace"])
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "primary.md").write_text("AI_TEAM_ARTIFACT_STATUS: in_progress\n# partial\n", encoding="utf-8")
            bridge.fail(entry["task_id"], "runtime failure")
            supervisor._refresh_entry(entry)
            self.assertFalse(supervisor._salvage_entry(entry, "primary.md"))
            self.assertNotIn("dsh", supervisor._usable(run["primary"], "primary.md"))

    def test_ready_artifact_while_running_triggers_immediate_finalization_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(goal="Stop after artifact readiness.", workspace=str(Path(tmp) / "work"), participants=["dsh", "qwen"])
            entry = run["primary"]["dsh"]
            task = bridge._tasks[entry["task_id"]]
            task["status"] = "running"
            task["started"] = True
            task["started_at"] = "2026-01-01T00:00:00+00:00"
            workspace = Path(entry["workspace"]); workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "primary.md").write_text("# decision ready\n", encoding="utf-8")
            run["phase_started_at"] = "2099-01-01T00:00:00+00:00"  # avoid normal soft timeout
            supervisor.runs.save(run)
            run = supervisor.advance(run["run_id"])
            self.assertTrue(any("artifact is already present and decision-ready" in msg for _, msg in bridge.messages))
            self.assertTrue(any("artifact ready while task still active" in w for w in run["warnings"]))


    def test_review_uses_external_worker_when_fallback_collapses_roles_to_one_producer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Keep review independent after fallback collapse.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "qwen"],
            )
            # Both role artifacts are represented as having been produced by DSH.
            for role_owner, entry in run["primary"].items():
                bridge.complete(entry["task_id"], "primary.md", f"# {role_owner}\n")
                entry["worker_id"] = "dsh"
            supervisor.runs.save(run)
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "review_running")
            self.assertEqual(len(run["reviews"]), 2)
            self.assertTrue(all(item["reviewer"] != item["target_worker"] for item in run["reviews"].values()))
            self.assertTrue(all(item["reviewer"] != "dsh" for item in run["reviews"].values()))
            self.assertEqual({item["target_role"] for item in run["reviews"].values()}, {"engineering_verifier", "implementation_architect"})


    def test_review_current_workspace_artifact_can_be_salvaged_without_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            workspace = Path(tmp) / "review"
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / "review.md"
            path.write_text(valid_review(), encoding="utf-8")
            entry = {
                "reviewer": "gemini",
                "task_id": "review-task",
                "workspace": str(workspace),
                "status": "cancelled",
                "failure_class": "timeout",
            }
            self.assertTrue(supervisor._salvage_entry(entry, "review.md"))
            self.assertEqual(Path(entry["salvaged_artifact"]), path)
            self.assertEqual(entry["salvaged_from_attempt"], 1)
            self.assertEqual(entry["salvaged_from_worker"], "gemini")
            self.assertFalse(supervisor._salvage_entry(entry, "review.md"))


    def test_finalized_artifact_may_mention_old_checkpoint_marker_in_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "primary.md"
            path.write_text(
                "AI_TEAM_ARTIFACT_STATUS: complete\n\n# Report\nThe worker replaced `AI_TEAM_ARTIFACT_STATUS: in_progress` during finalization.\n",
                encoding="utf-8",
            )
            self.assertTrue(TeamSupervisor._artifact_ready_path(path))

            path.write_text(
                "AI_TEAM_ARTIFACT_STATUS: in_progress\n\n# Report\nStill a checkpoint.\n",
                encoding="utf-8",
            )
            self.assertFalse(TeamSupervisor._artifact_ready_path(path))


    def test_failed_synthesis_can_late_recover_ready_final_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Recover a synthesis artifact that lands after timeout bookkeeping.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "qwen"],
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            synth = run["synthesis"]
            bridge.cancel(synth["task_id"])
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "failed")

            final_path = Path(synth["workspace"]) / "final.md"
            final_path.write_text("# Late but decision-ready final\n", encoding="utf-8")
            recovered = supervisor.advance(run["run_id"])
            self.assertEqual(recovered["status"], "completed")
            self.assertEqual(recovered["phase"], "completed")
            self.assertTrue(recovered["degraded"])
            self.assertEqual(Path(recovered["final_artifact"]), final_path)
            self.assertTrue(recovered["synthesis"]["salvaged"])
            self.assertEqual(recovered["synthesis"]["status"], "cancelled")
            self.assertTrue(any("late recovery salvaged final.md" in w for w in recovered["warnings"]))


    def test_missing_primary_role_is_recorded_even_when_quorum_is_met(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            run = supervisor.start_run(
                goal="Record every missing role even above quorum.",
                workspace=str(Path(tmp) / "work"),
                participants=["dsh", "gemini", "grok", "qwen"],
            )
            for role_owner in ("dsh", "gemini", "qwen"):
                bridge.complete(run["primary"][role_owner]["task_id"], "primary.md", f"# {role_owner}\n")
            bridge.fail(run["primary"]["grok"]["task_id"], "unauthorized")

            advanced = supervisor.advance(run["run_id"])
            self.assertEqual(advanced["status"], "review_running")
            self.assertFalse(advanced.get("degraded", False))
            self.assertEqual(
                advanced["coverage_gaps"]["missing_primary_roles"],
                ["trend_scout_red_team"],
            )

    def test_validation_timeout_reassigns_to_an_independent_worker_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            product_workspace = Path(tmp) / "product"
            run = supervisor.start_run(
                goal="Recover independent validation after validator timeout.",
                workspace=str(product_workspace),
                participants=["dsh", "qwen", "gemini"],
                implementation_enabled=True,
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            implementation = run["implementation"]
            bridge.complete(
                implementation["task_id"],
                implementation["artifact_relative"],
                "IMPLEMENTATION_STATUS: no_change\n## CHANGED_FILES\n- none\n## ACCEPTANCE_EVIDENCE\n- ok\n## TEST_RESULTS\n- pass\n## UNRESOLVED\n- none\n",
            )
            run = supervisor.advance(run["run_id"])
            original_validation = run["validation"]
            self.assertEqual(original_validation["agent_id"], "qwen")
            old_task = original_validation["task_id"]
            bridge.fail(
                old_task,
                "Qwen timed out after 60000ms",
                filename=original_validation["artifact_relative"],
                content="AI_TEAM_ARTIFACT_STATUS: in_progress\n# checkpoint\n",
            )

            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            self.assertEqual(run["status"], "validation_running")
            self.assertEqual(validation["reassignment_count"], 1)
            self.assertEqual(validation["agent_id"], "gemini")
            self.assertNotEqual(validation["task_id"], old_task)
            self.assertEqual(len(validation["attempts"]), 2)
            self.assertEqual(validation["attempts"][0]["failure_class"], "timeout")
            self.assertEqual(run["validation_reassignments"][0]["fallback_worker"], "gemini")
            self.assertIn("attempt-2-gemini", validation["artifact_relative"])

            bridge.complete(
                validation["task_id"],
                validation["artifact_relative"],
                "## ACCEPTED_CHECKS\n- pass\n## FAILED_CHECKS\n- none\n## DIFF_SCOPE\n- scoped\n## TEST_EVIDENCE\n- independently passed\n## REQUIRED_REPAIR\n- none\nVERDICT: accept\n",
            )
            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["implementation_validation_verdict"], "accept")

    def test_validation_plan_extracts_declared_test_commands_without_repo_search_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "implementation_report.md"
            report.write_text(
                "## TEST_RESULTS\n"
                "- `python3 -m unittest test_discovery test_chinese_discovery`\n"
                "- `python3 -m unittest discover -p 'test_*.py'`\n"
                "- `git diff --stat`\n"
                "- `grep -n bad discovery.py`\n",
                encoding="utf-8",
            )
            self.assertEqual(
                TeamSupervisor._declared_validation_commands(report),
                [
                    "python3 -m unittest test_discovery test_chinese_discovery",
                    "python3 -m unittest discover -p 'test_*.py'",
                ],
            )

    def test_legacy_validation_without_attempts_does_not_retry_same_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            product_workspace = Path(tmp) / "product"
            run = supervisor.start_run(
                goal="Recover old validation state safely.",
                workspace=str(product_workspace),
                participants=["dsh", "qwen", "gemini"],
                implementation_enabled=True,
            )
            for entry in run["primary"].values():
                bridge.complete(entry["task_id"], "primary.md", "# primary\n")
            run = supervisor.advance(run["run_id"])
            for entry in run["reviews"].values():
                bridge.complete(entry["task_id"], "review.md", valid_review())
            run = supervisor.advance(run["run_id"])
            bridge.complete(run["synthesis"]["task_id"], "final.md", "# Final\n")
            run = supervisor.advance(run["run_id"])
            bridge.complete(
                run["implementation"]["task_id"],
                run["implementation"]["artifact_relative"],
                "IMPLEMENTATION_STATUS: no_change\n## CHANGED_FILES\n- none\n## ACCEPTANCE_EVIDENCE\n- ok\n## TEST_RESULTS\n- `python3 -m unittest test_x` pass\n## UNRESOLVED\n- none\n",
            )
            run = supervisor.advance(run["run_id"])
            validation = run["validation"]
            self.assertEqual(validation["agent_id"], "qwen")
            validation.pop("attempts", None)
            validation["reassignment_count"] = 0
            old_task = validation["task_id"]
            bridge.fail(old_task, "Qwen timed out after 60000ms")
            supervisor.runs.save(run)

            run = supervisor.advance(run["run_id"])
            self.assertEqual(run["status"], "validation_running")
            self.assertEqual(run["validation"]["agent_id"], "gemini")
            self.assertNotEqual(run["validation"]["task_id"], old_task)
            self.assertEqual(run["validation"]["attempts"][0]["worker_id"], "qwen")
            self.assertTrue(run["validation"]["attempts"][0].get("legacy_seeded"))

    def test_later_validation_accept_moves_recovered_validation_errors_out_of_active_errors(self):
        run = {
            "errors": [
                "validation hard timeout reached after 240s; continuing in degraded mode",
                "implementation validation report missing a valid VERDICT",
                "unrelated product error",
            ]
        }
        TeamSupervisor._resolve_validation_errors(run)
        self.assertEqual(run["errors"], ["unrelated product error"])
        self.assertEqual(run["resolved_errors"][0]["phase"], "validation")
        self.assertEqual(len(run["resolved_errors"][0]["items"]), 2)


    def test_primary_context_artifact_is_staged_and_declared_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            workspace = Path(tmp) / "product"
            workspace.mkdir()
            handoff = workspace / "handoff.json"
            handoff.write_text('{"topic":"agent skills"}\n', encoding="utf-8")

            run = supervisor.start_run(
                goal="Use the supplied Radar handoff.",
                workspace=str(workspace),
                participants=["dsh", "qwen"],
                context_artifacts=["handoff.json"],
            )

            entry = run["primary"]["dsh"]
            staged = Path(entry["workspace"]) / "inputs" / "context" / "001-handoff.json"
            self.assertTrue(staged.is_file())
            task = bridge._tasks[entry["task_id"]]
            self.assertIn("Required context artifacts are staged under inputs/context/", task["prompt"])
            self.assertIn('"topic":"agent skills"', task["prompt"])
            self.assertEqual(task["metadata"]["context_artifacts"], ["inputs/context/001-handoff.json"])
            self.assertIn('"topic":"agent skills"', task["metadata"]["context_excerpt"])
            self.assertEqual(run["product_contract"]["context_artifacts"], ["handoff.json"])

    def test_context_artifact_cannot_escape_product_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            supervisor = TeamSupervisor(bridge, state_dir=Path(tmp) / "state")
            workspace = Path(tmp) / "product"
            workspace.mkdir()
            outside = Path(tmp) / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                supervisor.start_run(
                    goal="Reject escaped context.",
                    workspace=str(workspace),
                    participants=["dsh", "qwen"],
                    context_artifacts=["../outside.json"],
                )


if __name__ == "__main__":
    unittest.main()
