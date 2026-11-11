"""MCP facade for AI Team Orchestrator.

This server never launches workers directly. It exposes focused MCP tools that
delegate to the local Agent Bridge, preserving one source of truth for task
status, ACK semantics, messages, artifacts, and cancellation.
"""
from __future__ import annotations

import argparse
import os
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from supervisor import BridgeClient
from team_supervisor import TeamSupervisor
from product_intake import ProductContractStore
from goal_runner import GoalRunner
from radar_handoff_intake import draft_from_handoff
from release_report import generate_release_report


SERVER_VERSION = "0.9.0"

SERVER_INSTRUCTIONS = (
    "AI Team Orchestrator controls registered specialist agents. "
    "Before saying an agent has started, call get_task and verify started=true "
    "or status is acknowledged/running. queued means not started. "
    "Use task_id from submit_task for all follow-up calls. "
    "For normal goals, prefer run_goal: approved=false previews Product Contract + Delivery Plan "
    "without starting workers; approved=true starts execution from the same unified entry. "
    "For lower-level control, draft_product_contract creates a reviewable draft and "
    "approve_product_contract starts it explicitly."
)


def build_server(client: BridgeClient | None = None) -> MCPServer:
    bridge = client or BridgeClient(
        os.getenv("AGENT_BRIDGE_URL", "http://127.0.0.1:8765")
    )
    team = TeamSupervisor(
        bridge,
        state_dir=os.getenv("AI_TEAM_RUN_STATE_DIR", ".orchestrator/team-runs"),
    )
    contracts = ProductContractStore(
        os.getenv("AI_TEAM_PRODUCT_CONTRACT_DIR", ".orchestrator/product-contracts")
    )
    goals = GoalRunner(contracts=contracts, team=team)
    mcp = MCPServer(
        "ai-team-orchestrator",
        version=SERVER_VERSION,
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
        title="Get task progress",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def get_task_progress(task_id: str) -> dict[str, Any]:
        """Return worker-reported progress events. Progress never changes task truth."""
        return {"progress": bridge.progress(task_id)}

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

    @mcp.tool(
        title="Draft product contract（起草产品合同）",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def draft_product_contract(
        idea: str,
        workspace: str = ".",
        product_name: str | None = None,
        participants: list[str] | None = None,
    ) -> dict[str, Any]:
        """把一句模糊产品想法转成必须经用户批准的 Product Contract（产品合同）草案。"""
        return contracts.draft(
            idea=idea, workspace=workspace, product_name=product_name, participants=participants
        )

    @mcp.tool(
        title="Draft Radar handoff product contract（从 Radar 交接包起草产品合同）",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def draft_radar_handoff_contract(
        handoff_path: str,
        workspace: str,
        participants: list[str] | None = None,
    ) -> dict[str, Any]:
        """把 Radar HANDOFF.json 转成标准 Product Contract 草案；不会启动 Worker。"""
        return draft_from_handoff(
            handoff_path,
            workspace=workspace,
            store=contracts,
            participants=participants,
        )

    @mcp.tool(
        title="Run goal（目标执行入口）",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def run_goal(
        goal: str,
        workspace: str = ".",
        product_name: str | None = None,
        participants: list[str] | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        """一句话目标 → 产品合同 + 交付计划；approved=true 时从同一入口启动真实交付。"""
        return goals.submit(
            goal=goal,
            workspace=workspace,
            product_name=product_name,
            participants=participants,
            approved=approved,
        )

    @mcp.tool(
        title="Approve product contract and start（批准产品合同并启动）",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def approve_product_contract(contract_id: str, approved: bool) -> dict[str, Any]:
        """只有 approved=true（明确批准）时才启动 Team Run（团队运行）；拒绝不会启动任何任务。"""
        return contracts.decide_and_start(contract_id=contract_id, approved=approved, team=team)

    @mcp.tool(
        title="Start automatic multi-agent team run",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def start_team_run(
        goal: str,
        workspace: str = ".",
        participants: list[str] | None = None,
        acceptance: list[str] | None = None,
        evidence_required: list[str] | None = None,
        stop_conditions: list[str] | None = None,
        fallback_policy: str = "reassign|salvage|degrade",
        implementation_enabled: bool = False,
    ) -> dict[str, Any]:
        """Start a product-team run with explicit acceptance/evidence/stop contracts."""
        return team.start_run(
            goal=goal,
            workspace=workspace,
            participants=participants,
            acceptance=acceptance,
            evidence_required=evidence_required,
            stop_conditions=stop_conditions,
            fallback_policy=fallback_policy,
            implementation_enabled=implementation_enabled,
        )

    @mcp.tool(
        title="Get automatic team run",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def get_team_run(run_id: str) -> dict[str, Any]:
        """Get the multi-agent run graph and truthful task states."""
        return team.get_run(run_id)

    @mcp.tool(
        title="Get team run progress",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
    )
    def get_team_run_progress(run_id: str) -> dict[str, Any]:
        """Get compact machine-readable phase progress for a product-team run."""
        run = team.get_run(run_id)
        return run["progress"]

    @mcp.tool(
        title="Generate release report（生成发布报告）",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def generate_team_release_report(run_id: str) -> dict[str, Any]:
        """从真实 run state（运行状态）确定性生成用户可读发布报告，不重新调用模型总结。"""
        run = team.get_run(run_id, refresh=False)
        return generate_release_report(run)

    @mcp.tool(
        title="Advance automatic team run",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def advance_team_run(run_id: str) -> dict[str, Any]:
        """Manually advance one run; normally the background supervisor daemon does this."""
        return team.advance(run_id)

    return mcp


def run_server(
    mcp: MCPServer,
    *,
    transport: str,
    host: str = "127.0.0.1",
    port: int = 3000,
) -> None:
    """Run one MCP facade over HTTP or local stdio.

    stdio is the preferred transport when a local host such as WebCodex Runner
    owns the provider process. Streamable HTTP remains the default for the
    existing standalone MCP deployment path.
    """

    normalized = transport.strip().lower()
    if normalized == "stdio":
        mcp.run(transport="stdio")
        return
    if normalized == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=host,
            port=port,
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
        )
        return
    raise ValueError(
        f"unsupported MCP transport {transport!r}; expected 'stdio' or 'streamable-http'"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Team Orchestrator MCP facade")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.getenv("ORCHESTRATOR_MCP_TRANSPORT", "streamable-http"),
        help="MCP transport (default: streamable-http; use stdio for WebCodex local MCP gateway)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("ORCHESTRATOR_MCP_HOST", "127.0.0.1"),
        help="HTTP bind host; ignored for stdio",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ORCHESTRATOR_MCP_PORT", "3000")),
        help="HTTP bind port; ignored for stdio",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_server(
        build_server(),
        transport=args.transport,
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
