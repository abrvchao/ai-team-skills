"""聚合数据货币列表接口：解析结果与安全请求规划（不主动联网）。"""
from __future__ import annotations

from collections.abc import Mapping
from third_party_api import ApiSourcePlan, ApiGateError

ENDPOINT = "https://op.juhe.cn/onebox/exchange/list"


def currency_list_request(plan: ApiSourcePlan) -> dict[str, object]:
    """返回非敏感请求参数；真实 Key 只在运行时从环境变量获取。"""
    if plan.provider != "juhe" or plan.endpoint != ENDPOINT:
        raise ApiGateError("unsupported_currency_endpoint")
    plan.authorize()
    return {"method": "GET", "url": ENDPOINT, "version": "2",
            "key_env": plan.secret_env, "network_executed": False}


def normalize_currency_list(payload: Mapping[str, object]) -> list[dict[str, str]]:
    """仅解析成功响应，保留接口原始代码，不自行修正不规范币种码。"""
    if not isinstance(payload, Mapping) or payload.get("error_code") != 0:
        raise ApiGateError("currency_api_error")
    result = payload.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
        raise ApiGateError("invalid_currency_result")
    output = []
    seen = set()
    for entry in result["list"]:
        if not isinstance(entry, Mapping):
            raise ApiGateError("invalid_currency_entry")
        name, code = entry.get("name"), entry.get("code")
        if (not isinstance(name, str) or not name.strip()
                or not isinstance(code, str) or not 2 <= len(code) <= 5
                or not code.isalpha() or not code.isupper()):
            raise ApiGateError("invalid_currency_entry")
        if code not in seen:
            output.append({"name": name.strip(), "code": code})
            seen.add(code)
    return output
