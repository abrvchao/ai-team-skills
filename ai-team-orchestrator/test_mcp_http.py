from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path

from mcp import Client


ROOT = Path(__file__).resolve().parent


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class MCPHTTPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamable_http_server_lists_expected_tools(self):
        port = free_port()
        env = os.environ.copy()
        env["ORCHESTRATOR_MCP_HOST"] = "127.0.0.1"
        env["ORCHESTRATOR_MCP_PORT"] = str(port)
        env["AGENT_BRIDGE_URL"] = "http://127.0.0.1:65534"

        process = subprocess.Popen(
            [sys.executable, "mcp_server.py"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            deadline = asyncio.get_running_loop().time() + 10
            names: set[str] | None = None
            last_error: Exception | None = None

            while asyncio.get_running_loop().time() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=1)
                    self.fail(
                        f"MCP server exited early with {process.returncode}\n"
                        f"stdout={stdout}\nstderr={stderr}"
                    )
                try:
                    async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                        result = await client.list_tools()
                        names = {tool.name for tool in result.tools}
                    break
                except Exception as exc:
                    last_error = exc
                    await asyncio.sleep(0.1)

            if names is None:
                self.fail(f"unable to connect to MCP HTTP server: {last_error}")

            self.assertEqual(
                names,
                {
                    "list_agents",
                    "list_tasks",
                    "get_task",
                    "list_artifacts",
                    "submit_task",
                    "send_message",
                    "cancel_task",
                },
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
