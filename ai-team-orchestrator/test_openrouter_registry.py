"""OpenRouter 双身份必须默认禁用，防止意外计费。"""
import unittest

from registry import AgentRegistry


class OpenRouterRegistryTests(unittest.TestCase):
    def test_two_openrouter_workers_are_distinct_and_off_by_default(self):
        agents = AgentRegistry.default(dsh_configured=False)
        for agent_id in ("openrouter_1", "openrouter_2"):
            worker = agents.get(agent_id)
            self.assertFalse(worker.configured)
            self.assertFalse(worker.available)
            self.assertFalse(worker.queueable)
            self.assertEqual(worker.transport, "not_configured")

    def test_explicit_queued_config_is_not_live_availability(self):
        agents = AgentRegistry.default(
            dsh_configured=False,
            configured_pull_agents={"openrouter_1", "openrouter_2"},
        )
        for agent_id in ("openrouter_1", "openrouter_2"):
            worker = agents.get(agent_id)
            self.assertTrue(worker.configured)
            self.assertTrue(worker.queueable)
            self.assertFalse(worker.available)


if __name__ == "__main__":
    unittest.main()
