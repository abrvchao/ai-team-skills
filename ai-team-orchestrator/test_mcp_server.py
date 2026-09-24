from __future__ import annotations

import unittest

from mcp import Client

from mcp_server import build_server


class FakeBridgeClient:
    def __init__(self) -> None:
        self.write_calls: list[tuple] = []
        self.read_calls: list[tuple] = []

    def agents(self):
        self.read_calls.append(("agents",))
        return [
            {
                "agent_id": "dsh",
                "configured": True,
                "available": True,
                "transport": "local_bridge",
            },
            {
                "agent_id": "gemini",
                "configured": False,
                "available": False,
                "transport": "not_configured",
            },
        ]

    def tasks(self):
        self.read_calls.append(("tasks",))
        return [
            {
                "task_id": "task_1",
                "agent_id": "dsh",
                "status": "queued",
                "started": False,
            }
        ]

    def task(self, task_id: str):
        self.read_calls.append(("task", task_id))
        return {
            "task_id": task_id,
            "agent_id": "dsh",
            "status": "queued",
            "started": False,
            "started_at": None,
        }

    def artifacts(self, task_id: str):
        self.read_calls.append(("artifacts", task_id))
        return [{"artifact_id": "a1", "task_id": task_id, "kind": "file", "path": "workspace/x"}]

    def submit(self, **kwargs):
        self.write_calls.append(("submit", kwargs))
        return {
            "task_id": "task_new",
            "agent_id": kwargs["agent_id"],
            "status": "queued",
            "started": False,
            "started_at": None,
        }

    def message(self, task_id: str, message: str):
        self.write_calls.append(("message", task_id, message))
        return {
            "task_id": task_id,
            "agent_id": "dsh",
            "status": "running",
            "started": True,
        }

    def cancel(self, task_id: str):
        self.write_calls.append(("cancel", task_id))
        return {
            "task_id": task_id,
            "agent_id": "dsh",
            "status": "cancelled",
            "started": True,
        }


class MCPFacadeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge = FakeBridgeClient()
        self.server = build_server(self.bridge)

    async def test_tool_list_and_annotations(self):
        async with Client(self.server) as client:
            result = await client.list_tools()

        tools = {tool.name: tool for tool in result.tools}
        expected = {
            "list_agents",
            "list_tasks",
            "get_task",
            "list_artifacts",
            "submit_task",
            "send_message",
            "cancel_task",
        }
        self.assertEqual(set(tools), expected)

        self.assertTrue(tools["list_agents"].annotations.read_only_hint)
        self.assertTrue(tools["get_task"].annotations.read_only_hint)
        self.assertFalse(tools["submit_task"].annotations.read_only_hint)
        self.assertTrue(tools["submit_task"].annotations.open_world_hint)
        self.assertTrue(tools["cancel_task"].annotations.destructive_hint)

    async def test_read_tools_do_not_write(self):
        async with Client(self.server) as client:
            agents = await client.call_tool("list_agents", {})
            tasks = await client.call_tool("list_tasks", {})
            task = await client.call_tool("get_task", {"task_id": "task_1"})
            artifacts = await client.call_tool("list_artifacts", {"task_id": "task_1"})

        self.assertEqual(self.bridge.write_calls, [])
        self.assertEqual(agents.structured_content["agents"][0]["agent_id"], "dsh")
        self.assertFalse(tasks.structured_content["tasks"][0]["started"])
        self.assertEqual(task.structured_content["status"], "queued")
        self.assertEqual(artifacts.structured_content["artifacts"][0]["artifact_id"], "a1")

    async def test_submit_preserves_queued_not_started_truth(self):
        async with Client(self.server) as client:
            result = await client.call_tool(
                "submit_task",
                {
                    "agent_id": "dsh",
                    "title": "Implement feature",
                    "prompt": "Make the change and run tests.",
                    "workspace": ".",
                },
            )

        self.assertEqual(result.structured_content["status"], "queued")
        self.assertFalse(result.structured_content["started"])
        self.assertIsNone(result.structured_content["started_at"])
        submits = [row for row in self.bridge.write_calls if row[0] == "submit"]
        self.assertEqual(len(submits), 1)

    async def test_message_and_cancel_delegate_once(self):
        async with Client(self.server) as client:
            message = await client.call_tool(
                "send_message",
                {"task_id": "task_1", "message": "Also run integration tests."},
            )
            cancelled = await client.call_tool(
                "cancel_task",
                {"task_id": "task_1"},
            )

        self.assertEqual(message.structured_content["status"], "running")
        self.assertEqual(cancelled.structured_content["status"], "cancelled")
        self.assertEqual(
            [row[0] for row in self.bridge.write_calls],
            ["message", "cancel"],
        )

    async def test_unavailable_agent_is_visible_not_hidden(self):
        async with Client(self.server) as client:
            result = await client.call_tool("list_agents", {})

        gemini = next(
            item
            for item in result.structured_content["agents"]
            if item["agent_id"] == "gemini"
        )
        self.assertFalse(gemini["available"])
        self.assertFalse(gemini["configured"])


if __name__ == "__main__":
    unittest.main()
