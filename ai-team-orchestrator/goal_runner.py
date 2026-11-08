"""Goal Runner（目标执行入口）: one goal → plan preview or approved Team Run."""
from __future__ import annotations

from typing import Any

from product_intake import ProductContractStore


class GoalRunner:
    """统一 Product Contract（产品合同）与 Delivery Plan（交付计划）入口。"""

    def __init__(self, *, contracts: ProductContractStore, team: Any) -> None:
        self.contracts = contracts
        self.team = team

    def submit(
        self,
        *,
        goal: str,
        workspace: str = ".",
        product_name: str | None = None,
        participants: list[str] | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        """默认零 Worker 成本预览；approved=true 时从同一入口启动真实交付。"""
        contract = self.contracts.draft(
            idea=goal,
            workspace=workspace,
            product_name=product_name,
            participants=participants,
        )
        plan = self.team.plan_run(
            goal=contract["objective"],
            participants=contract.get("participants") or None,
            fallback_policy=contract.get("fallback_policy") or "reassign|salvage|degrade",
            implementation_enabled=bool(contract.get("implementation_enabled", True)),
        )

        if not approved:
            return {
                "mode": "preview",
                "started": False,
                "contract": contract,
                "plan": plan,
                "next_action": "approve this Goal Runner plan to start execution（批准该计划后启动执行）",
            }

        started_contract = self.contracts.decide_and_start(
            contract_id=contract["contract_id"],
            approved=True,
            team=self.team,
        )
        run = self.team.get_run(started_contract["run_id"], refresh=False)
        return {
            "mode": "started",
            "started": True,
            "contract": started_contract,
            "run_id": run["run_id"],
            "status": run["status"],
            "phase": run["phase"],
            "participants": run["participants"],
            "delivery_plan": run["delivery_plan"],
            "progress": run["progress"],
            "next_action": (run.get("progress") or {}).get("next_action"),
        }
