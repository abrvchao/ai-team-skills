"""聚合数据汇率响应解析：只处理离线 JSON，不调用付费接口。"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from third_party_api import ApiGateError, ApiSourcePlan

ENDPOINT = "https://op.juhe.cn/onebox/exchange/currency"


def rate_request(plan: ApiSourcePlan) -> dict[str, object]:
    """生成调用规划；Key 必须由安全环境变量提供，此处不加载。"""
    if plan.provider != "juhe" or plan.endpoint != ENDPOINT:
        raise ApiGateError("unsupported_rate_endpoint")
    plan.authorize()
    return {"method": "GET", "url": ENDPOINT, "key_env": plan.secret_env,
            "network_executed": False}


def normalize_rates(payload: Mapping[str, object], *, now: datetime | None = None,
                    max_age: timedelta = timedelta(days=1)) -> list[dict[str, object]]:
    """按字段明确解析；旧数据标记过期，不会伪装成实时汇率。"""
    if not isinstance(payload, Mapping) or payload.get("error_code") != 0:
        raise ApiGateError("exchange_api_error")
    records = payload.get("result")
    if not isinstance(records, list):
        raise ApiGateError("invalid_exchange_result")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or max_age.total_seconds() <= 0:
        raise ApiGateError("invalid_clock_or_max_age")
    output = []
    for entry in records:
        if not isinstance(entry, Mapping):
            raise ApiGateError("invalid_rate_entry")
        source, target = entry.get("currencyF"), entry.get("currencyT")
        if any(not isinstance(c, str) or len(c) != 3 or not c.isascii()
               or not c.isalpha() or not c.isupper() for c in (source, target)):
            raise ApiGateError("invalid_currency_code")
        try:
            raw_rate = entry["exchange"]
            if isinstance(raw_rate, bool):
                raise InvalidOperation
            rate = Decimal(str(raw_rate))
            amount = Decimal(str(entry["currencyFD"]))
            updated = datetime.strptime(entry["updateTime"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ApiGateError("invalid_rate_value") from exc
        if not rate.is_finite() or rate <= 0 or not amount.is_finite() or amount <= 0:
            raise ApiGateError("invalid_rate_value")
        # 文档中的时间没有时区；只能暂存为未知时区，不能推断其绝对新鲜度。
        output.append({"from": source, "to": target, "base_amount": str(amount),
                       "rate": str(rate), "display_result": str(entry.get("result", "")),
                       "update_time_local": updated.isoformat(sep=" "),
                       "timezone_known": False, "freshness": "unverified",
                       "usable_as_live_rate": False})
    return output
