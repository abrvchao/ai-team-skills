"""第三方 API 离线配置与预算门禁；此模块绝不执行网络请求。"""
from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit

# 极速数据订阅已到期：暂停授权，任何旧配置也不可继续调用。
ALLOWED_HOSTS = {"juhe": "apis.juhe.cn"}
ALLOWED_ENDPOINTS = {"juhe": frozenset({
    "https://op.juhe.cn/onebox/exchange/list",
    "https://op.juhe.cn/onebox/exchange/currency",
})}

class ApiGateError(ValueError):
    """对外仅返回安全错误码，不包含密钥、URL 参数或请求正文。"""

@dataclass(frozen=True)
class ApiSourcePlan:
    provider: str
    endpoint: str
    secret_env: str
    enabled: bool = False
    approved_paid: bool = False
    unit_price_cny: float | None = None
    max_calls: int = 0
    max_cost_cny: float = 0

    def authorize(self, calls: int = 1) -> dict:
        # 只接受官方 HTTPS 域名，不允许 URL 中嵌入 Key；未知计费一律拒绝。
        if self.provider not in ALLOWED_HOSTS:
            raise ApiGateError("unsupported_provider")
        try:
            url = urlsplit(self.endpoint)
            valid = (url.scheme == "https" and url.hostname in
                     ({ALLOWED_HOSTS[self.provider]} | {"op.juhe.cn"} if self.provider == "juhe" else {ALLOWED_HOSTS[self.provider]})
                     and url.port in (None, 443) and not url.username and not url.password
                     and not url.query and not url.fragment
                     and (url.hostname != "op.juhe.cn" or self.endpoint in ALLOWED_ENDPOINTS["juhe"]))
        except ValueError:
            valid = False
        if not valid:
            raise ApiGateError("endpoint_not_allowed")
        if not self.secret_env.isidentifier() or self.secret_env != self.secret_env.upper():
            raise ApiGateError("invalid_secret_reference")
        if not self.enabled:
            raise ApiGateError("source_disabled")
        if type(calls) is not int or calls < 1 or calls > self.max_calls:
            raise ApiGateError("call_limit_exceeded")
        price = self.unit_price_cny
        if price is None:
            raise ApiGateError("price_unknown")
        if type(price) not in (int, float) or not isfinite(price) or price < 0:
            raise ApiGateError("invalid_price")
        estimate = round(price * calls, 6)
        if price > 0 and not self.approved_paid:
            raise ApiGateError("paid_calls_not_approved")
        if not isfinite(self.max_cost_cny) or self.max_cost_cny < estimate:
            raise ApiGateError("budget_exceeded")
        return {"provider": self.provider, "calls": calls,
                "estimated_cost_cny": estimate, "network_executed": False}
