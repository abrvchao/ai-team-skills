"""Agent routing layer.

The Dispatcher chooses a registered adapter; it does not know transport details.
Future Gemini/Grok/Codex adapters plug in here without changing Supervisor.
"""
from __future__ import annotations

from adapters import AgentAdapter
from registry import AgentRegistry


class Dispatcher:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        adapters: dict[str, AgentAdapter],
    ) -> None:
        self.registry = registry
        self.adapters = dict(adapters)

    def adapter(self, agent_id: str) -> AgentAdapter:
        descriptor = self.registry.get(agent_id)
        adapter = self.adapters.get(agent_id)
        if descriptor.configured and adapter is not None:
            return adapter
        raise RuntimeError(
            f"agent {agent_id} is not available: "
            f"{descriptor.note or descriptor.transport}"
        )
