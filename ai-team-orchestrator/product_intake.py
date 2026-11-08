"""Project intake（项目接入）: vague idea → reviewable Product Contract draft（产品合同草案）."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTRACT_DRAFT_VERSION = "product-contract-draft-v0.1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    # 中文说明：合同属于控制面真相源，必须原子写入，避免进程中断留下半份 JSON。
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ProductContractStore:
    """Persist Product Contract drafts（持久化产品合同草案）并把批准动作交给 TeamSupervisor。"""

    def __init__(self, root: str | Path = ".orchestrator/product-contracts") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, contract_id: str) -> Path:
        return self.root / f"{contract_id}.json"

    def get(self, contract_id: str) -> dict[str, Any]:
        path = self.path(contract_id)
        if not path.is_file():
            raise KeyError(f"unknown product contract: {contract_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def draft(
        self,
        *,
        idea: str,
        workspace: str = ".",
        product_name: str | None = None,
        participants: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_idea = " ".join(str(idea or "").split()).strip()
        if len(clean_idea) < 4:
            raise ValueError("idea must contain a meaningful product goal")

        contract_id = "contract_" + secrets.token_hex(8)
        # 中文说明：这里先生成“可审阅骨架”，不假装 AI 已经理解所有产品细节。
        # 后续 Worker 会在真实 research/review 中补证据，但在用户批准前绝不启动执行。
        display_name = (product_name or clean_idea[:48]).strip()
        draft = {
            "version": CONTRACT_DRAFT_VERSION,
            "contract_id": contract_id,
            "status": "draft",
            "approval_required": True,
            "approved": False,
            "created_at": _utcnow(),
            "approved_at": None,
            "product_name": display_name,
            "idea": clean_idea,
            "workspace": str(workspace or "."),
            "participants": list(participants or []),
            "objective": clean_idea,
            "acceptance": [
                "A runnable product result exists in the requested workspace and directly addresses the stated objective.",
                "Critical product behavior has executable acceptance evidence rather than narrative-only claims.",
                "Independent validation reports an explicit accept verdict before the project is considered complete.",
            ],
            "evidence_required": [
                "Inspect the existing workspace and preserve unrelated user work.",
                "Capture before/after evidence for product behavior changed by this project.",
                "Run the smallest relevant tests first, then the appropriate regression suite before final acceptance.",
            ],
            "stop_conditions": [
                "Do not add unrelated model vendors, providers, infrastructure, or product scope.",
                "Prefer the smallest implementation that satisfies the accepted product contract.",
                "Stop exploration when acceptance can be evaluated and finish the mandatory artifact instead.",
                "For experimental or unvalidated products, define an explicit stop-loss before broadening scope.",
                "Any repeated broad multi-worker run must name the concrete gate or blocker it will resolve.",
            ],
            "resource_policy": {
                "mode": "bounded",
                "stop_loss_required_for_experimental_products": True,
                "repeat_multi_worker_run_requires_gate": True,
                "prefer_minimum_sufficient_workers": True,
                "truth_note": (
                    "Do not claim a token budget unless provider usage is auditable; "
                    "control spend through bounded workers, retries, phases, and explicit stop-loss gates."
                ),
            },
            "fallback_policy": "reassign|salvage|degrade",
            "implementation_enabled": True,
            "context_artifacts": [],
            "run_id": None,
            "approval_note": "User approval is required before any Team Run（团队运行） starts.",
        }
        _atomic_json(self.path(contract_id), draft)
        return draft

    def decide_and_start(
        self,
        *,
        contract_id: str,
        approved: bool,
        team: Any,
    ) -> dict[str, Any]:
        contract = self.get(contract_id)

        # 中文说明：批准动作设计成幂等。重复点击批准不能重复启动两个团队运行。
        if contract.get("status") == "started" and contract.get("run_id"):
            return contract
        if contract.get("status") == "rejected":
            return contract

        if not approved:
            contract["status"] = "rejected"
            contract["approved"] = False
            contract["approval_required"] = False
            contract["approved_at"] = _utcnow()
            _atomic_json(self.path(contract_id), contract)
            return contract

        participants = contract.get("participants") or None
        run = team.start_run(
            goal=contract["objective"],
            workspace=contract["workspace"],
            participants=participants,
            acceptance=contract.get("acceptance") or None,
            evidence_required=contract.get("evidence_required") or None,
            stop_conditions=contract.get("stop_conditions") or None,
            fallback_policy=contract.get("fallback_policy") or "reassign|salvage|degrade",
            implementation_enabled=bool(contract.get("implementation_enabled", True)),
            context_artifacts=contract.get("context_artifacts") or None,
            deliverable_artifacts=contract.get("deliverable_artifacts") or None,
        )
        contract["status"] = "started"
        contract["approved"] = True
        contract["approval_required"] = False
        contract["approved_at"] = _utcnow()
        contract["run_id"] = run["run_id"]
        _atomic_json(self.path(contract_id), contract)
        return contract
