"""汇率接口的纯离线契约测试。"""
import unittest
from juhe_exchange_rate import ENDPOINT, rate_request, normalize_rates
from third_party_api import ApiSourcePlan, ApiGateError


class JuheExchangeRateTest(unittest.TestCase):
    def test_documented_mixed_result_types_and_old_timestamp(self):
        sample = {"error_code": 0, "reason": "查询成功", "result": [
            {"currencyF": "JPY", "currencyF_Name": "日元", "currencyT": "BHD",
             "currencyT_Name": "巴林第纳尔", "currencyFD": 1,
             "exchange": "0.0032685972", "result": "0.0032",
             "updateTime": "2014-11-07 13:58:02"},
            {"currencyF": "BHD", "currencyT": "JPY", "currencyFD": 1,
             "exchange": "305.9416445623", "result": 305.9416,
             "updateTime": "2014-11-07 13:58:01"}]}
        rows = normalize_rates(sample)
        self.assertEqual(rows[0]["rate"], "0.0032685972")
        self.assertEqual(rows[1]["display_result"], "305.9416")
        self.assertTrue(all(not row["usable_as_live_rate"] for row in rows))

    def test_safe_request_plan(self):
        plan = ApiSourcePlan("juhe", ENDPOINT, "JUHE_API_KEY",
                             enabled=True, unit_price_cny=0, max_calls=1)
        self.assertFalse(rate_request(plan)["network_executed"])

    def test_bad_rate_or_error_rejected(self):
        with self.assertRaises(ApiGateError):
            normalize_rates({"error_code": 10001, "result": []})
        with self.assertRaises(ApiGateError):
            normalize_rates({"error_code": 0, "result": [
                {"currencyF": "JPY", "currencyT": "BHD", "currencyFD": 1,
                 "exchange": "-1", "updateTime": "2014-11-07 13:58:02"}]})

if __name__ == "__main__":
    unittest.main()
