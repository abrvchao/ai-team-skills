"""MCP facade for AI Team Orchestrator.

This server never launches workers directly. It exposes focused MCP tools that
delegate to the local Agent Bridge, preserving one source of truth for task
status, ACK semantics, messages, artifacts, and cancellation.
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from supervisor import BridgeClient


SERVER_INSTRUCTIONS = (
    "AI Team Orchestrator controls registered specialist agents. "
    "Before saying an agent has started, call get_task and verify started=true "
    "or status is acknowledged/running. queued means not started. "
    "Use task_id from submit_task for all follow-up calls."
)


def build_server(client: BridgeClient | None = None) -> MCPServer:
    bridge = client or BridgeClient(
        os.getenv("AGENT_BRIDGE_URL", "http://127.0.0.1:8765")
    )
    mcp = MCPServer(
        "ai-team-orchestrator",
        instructions=SERVER_INSTRUCTIONS,
    )

    @mcp.tool(
        title="List AI team agents",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def list_agents() -> dict[str, Any]:
        """List registered agents and whether each one is actually configured."""
        return {"agents": bridge.agents()}

    @mcp.tool(
        title="List orchestrator tasks",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def list_tasks() -> dict[str, Any]:
        """List recent tasks with their truthful bridge-derived status."""
        return {"tasks": bridge.tasks()}

    @mcp.tool(
        title="Get task status",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def get_task(task_id: str) -> dict[str, Any]:
        """Get one task. queued means not started; rely on started/started_at."""
        return bridge.task(task_id)

    @mcp.tool(
        title="List task artifacts",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def list_artifacts(task_id: str) -> dict[str, Any]:
        """List artifacts registered by the worker for a task."""
        return {"artifacts": bridge.artifacts(task_id)}

    @mcp.tool(
        title="Submit task to an AI agent",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def submit_task(
        agent_id: str,
        title: str,
        prompt: str,
        workspace: str = ".",
    ) -> dict[str, Any]:
        """Submit a real task. A returned queued task has NOT started yet."""
        return bridge.submit(
            agent_id=agent_id,
            title=title,
            prompt=prompt,
            workspace=workspace,
        )

    @mcp.tool(
        title="Send follow-up message to a task",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def send_message(task_id: str, message: str) -> dict[str, Any]:
        """Send an additional instruction to a non-terminal running task."""
        return bridge.message(task_id, message)

    @mcp.tool(
        title="Cancel AI agent task",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def cancel_task(task_id: str) -> dict[str, Any]:
        """Stop a queued/running task. Cancellation can interrupt active work."""
        return bridge.cancel(task_id)

    return mcp


def main() -> int:
    host = os.getenv("ORCHESTRATOR_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("ORCHESTRATOR_MCP_PORT", "3000"))
    mcp = build_server()
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
