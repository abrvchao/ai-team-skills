"""AI Team Node/CLI 调用费用账本的离线授权入口。

只有 reserve 子命令；绝不提供自动开通预算或重置费用的命令。
上游 Worker 必须检查返回码，只有首次成功预留才可发送一次真实付费请求。
"""
from __future__ import annotations

import argparse
import json
import sys

from cost_ledger import BudgetLedger, CostGateDenied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Team one-shot paid-call budget gate")
    parser.add_argument("--db", required=True, help="现有受控费用账本路径")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--unit-price-micros", type=int, required=True)
    parser.add_argument("--units", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        outcome = BudgetLedger(args.db).reserve(
            provider=args.provider,
            task_id=args.task_id,
            request_id=args.request_id,
            currency=args.currency,
            unit_price_micros=args.unit_price_micros,
            units=args.units,
        )
        if not outcome.may_execute_once:
            # 重放的 ID 已可能真实扣费，不能因此重复授予执行权。
            print(json.dumps({"allowed": False, "reason": "duplicate_request"}, ensure_ascii=False))
            return 3
        print(json.dumps({"allowed": True, "status": "reserved_unsettled"}, ensure_ascii=False))
        return 0
    except CostGateDenied as exc:
        # 仅返回受控错误码，不记录密钥/路径/请求正文。
        print(json.dumps({"allowed": False, "reason": str(exc)}, ensure_ascii=False))
        return 3
    except (OSError, ValueError, TypeError):
        print(json.dumps({"allowed": False, "reason": "gate_unavailable"}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
