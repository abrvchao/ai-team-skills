"""聚合数据货币列表：离线契约测试，不调用真实接口。"""
import unittest
from juhe_currency import ENDPOINT, currency_list_request, normalize_currency_list
from third_party_api import ApiGateError, ApiSourcePlan


class JuheCurrencyTests(unittest.TestCase):
    def test_request_uses_version_two_without_leaking_key(self):
        plan = ApiSourcePlan("juhe", ENDPOINT, "JUHE_API_KEY",
                             enabled=True, unit_price_cny=0, max_calls=1)
        request = currency_list_request(plan)
        self.assertEqual(request["version"], "2")
        self.assertEqual(request["key_env"], "JUHE_API_KEY")
        self.assertFalse(request["network_executed"])
        self.assertNotIn("key=", str(request))

    def test_parse_documented_response(self):
        rows = normalize_currency_list({"reason": "查询成功", "error_code": 0,
                                        "result": {"list": [
                                            {"name": "人民币", "code": "CNY"},
                                            {"name": "美元", "code": "USD"},
                                            {"name": "韩元", "code": "KER"}]}})
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["code"], "KER")

    def test_failure_responses_rejected(self):
        for payload in ({"error_code": 10001, "result": {}},
                        {"error_code": 0, "result": "bad"},
                        {"error_code": 0, "result": {"list": [{"code": "USD"}]}}):
            with self.subTest(payload=payload), self.assertRaises(ApiGateError):
                normalize_currency_list(payload)

if __name__ == "__main__":
    unittest.main()
