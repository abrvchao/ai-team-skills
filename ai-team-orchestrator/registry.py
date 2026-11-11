"""Agent registry and capability advertisement."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class AgentDescriptor:
    agent_id: str
    display_name: str
    configured: bool
    available: bool
    capabilities: list[str]
    transport: str
    queueable: bool = False
    note: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentDescriptor] = {}

    def register(self, agent: AgentDescriptor) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"agent already registered: {agent.agent_id}")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AgentDescriptor:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent: {agent_id}") from exc

    def list(self) -> list[AgentDescriptor]:
        return [self._agents[key] for key in sorted(self._agents)]

    @classmethod
    def default(
        cls,
        *,
        dsh_configured: bool,
        dsh_available: bool | None = None,
        dsh_queueable: bool | None = None,
        configured_pull_agents: set[str] | None = None,
    ) -> "AgentRegistry":
        registry = cls()
        pull_agents = set(configured_pull_agents or set())
        available = dsh_configured if dsh_available is None else dsh_available
        queueable = dsh_configured if dsh_queueable is None else dsh_queueable
        registry.register(
            AgentDescriptor(
                agent_id="dsh",
                display_name="DSH",
                configured=dsh_configured,
                available=available,
                capabilities=["implementation", "testing", "debugging", "git"],
                transport="local_bridge",
                queueable=queueable,
                note=(
                    ""
                    if available
                    else "DSH is configured/queueable but no active worker is online"
                    if dsh_configured
                    else "DSH transport is not configured"
                ),
            )
        )
        for agent_id, name, caps in (
            ("gemini", "Gemini", ["research", "long_context", "multimodal"]),
            ("grok", "Grok", ["research", "trend_analysis", "adversarial_review"]),
            ("qwen", "Qwen", ["implementation", "research", "long_context"]),
            ("mimo", "Xiaomi MiMo", ["research", "chinese_content", "code_review"]),
            ("nvidia", "NVIDIA NIM", ["research", "long_context", "code_review"]),
            ("workbuddy", "WorkBuddy Code", ["implementation", "code_review", "research"]),
            ("opencode", "OpenCode (MiMo Free, read-only)", ["research", "code_review", "planning"]),
            ("muse", "Muse", ["implementation", "long_context", "multimodal", "code_review"]),
            ("doubao", "Doubao", ["research", "chinese_content", "multimodal"]),
            ("codex", "Codex", ["implementation", "testing", "code_review"]),
            # 中文说明：两个 OpenRouter 身份独立注册，但不配置默认模型或自动启用。
            # 必须由运营者分别确认模型、价格、预算及授权后才能接单。
            ("openrouter_1", "OpenRouter Worker 1 (cost-gated)", ["research", "code_review", "planning"]),
            ("openrouter_2", "OpenRouter Worker 2 (cost-gated)", ["research", "code_review", "planning"]),
            # 中文说明：browser 是执行面，不加入 TeamSupervisor 的模型角色 TEAM_ORDER。
            ("browser", "Browser Worker", ["browser_navigation", "dom_capture", "screenshot", "read_only_web_evidence"]),
            # 中文说明：computer 同样是执行面，不参与模型角色分工。
            ("computer", "Local Computer Worker", ["gui_navigation", "app_activation", "gui_state_evidence", "read_only_computer_evidence"]),
            # 中文说明：android 是真实设备执行面，不参与模型角色分工。
            ("android", "Android Device Worker", ["android_device_info", "android_screenshot", "read_only_device_evidence"]),
        ):
            configured = agent_id in pull_agents
            registry.register(
                AgentDescriptor(
                    agent_id=agent_id,
                    display_name=name,
                    configured=configured,
                    available=False,
                    capabilities=caps,
                    transport="registered_pull" if configured else "not_configured",
                    queueable=configured,
                    note=(
                        f"{name} is queueable; no active worker is online"
                        if configured
                        else "Adapter placeholder only in V0.1"
                    ),
                )
            )
        return registry
