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
    note: str = ""

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
    def default(cls, *, dsh_configured: bool) -> "AgentRegistry":
        registry = cls()
        registry.register(
            AgentDescriptor(
                agent_id="dsh",
                display_name="DSH",
                configured=dsh_configured,
                available=dsh_configured,
                capabilities=["implementation", "testing", "debugging", "git"],
                transport="local_bridge",
                note="" if dsh_configured else "DSH command is not configured",
            )
        )
        for agent_id, name, caps in (
            ("gemini", "Gemini", ["research", "long_context", "multimodal"]),
            ("grok", "Grok", ["research", "trend_analysis", "adversarial_review"]),
            ("codex", "Codex", ["implementation", "testing", "code_review"]),
        ):
            registry.register(
                AgentDescriptor(
                    agent_id=agent_id,
                    display_name=name,
                    configured=False,
                    available=False,
                    capabilities=caps,
                    transport="not_configured",
                    note="Adapter placeholder only in V0.1",
                )
            )
        return registry
