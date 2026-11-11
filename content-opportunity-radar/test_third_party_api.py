"""仅用虚构配置测试；不调用真实数据服务。"""
import unittest
from dataclasses import replace
from third_party_api import ApiSourcePlan, ApiGateError

class TestThirdPartyApi(unittest.TestCase):
    def setUp(self):
        self.plan = ApiSourcePlan("juhe", "https://apis.juhe.cn/example",
                                  "JUHE_API_KEY", True, True, 0.1, 5, 0.3)

    def test_juhe_offline_configuration(self):
        self.assertFalse(self.plan.authorize(2)["network_executed"])
        self.assertEqual(self.plan.authorize()["provider"], "juhe")

    def test_expired_jisu_is_rejected_even_with_old_approval(self):
        expired = replace(self.plan, provider="jisu", endpoint="https://api.jisuapi.com/agent/search")
        with self.assertRaisesRegex(ApiGateError, "unsupported_provider"):
            expired.authorize()

    def test_fail_closed(self):
        for kwargs in ({"enabled": False}, {"unit_price_cny": None},
                       {"approved_paid": False}, {"max_cost_cny": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ApiGateError):
                replace(self.plan, **kwargs).authorize()

    def test_domain_and_budget(self):
        with self.assertRaisesRegex(ApiGateError, "call_limit_exceeded"):
            self.plan.authorize(6)
        with self.assertRaisesRegex(ApiGateError, "budget_exceeded"):
            self.plan.authorize(4)
        for endpoint in ("http://apis.juhe.cn/example",
                         "https://apis.juhe.cn.evil.test/a",
                         "https://apis.juhe.cn/example?key=secret"):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ApiGateError, "endpoint_not_allowed"):
                replace(self.plan, endpoint=endpoint).authorize()

    def test_free_mode(self):
        free = replace(self.plan, unit_price_cny=0, approved_paid=False, max_cost_cny=0)
        self.assertEqual(free.authorize()["estimated_cost_cny"], 0)

if __name__ == "__main__":
    unittest.main()
