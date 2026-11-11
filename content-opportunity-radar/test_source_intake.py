"""Only synthetic offline fixtures. No device, API credentials, or platform requests."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from source_intake import SourceGrant, SourceGateError, accept_candidate
from test_authority_fixtures import SyntheticAuthority

NOW = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)


def grant(platform="zhihu", pathway="official_api", **changes):
    baseline = SourceGrant(
        platform=platform, pathway=pathway, grant_ref="permission-test-001",
        expires_at=NOW + timedelta(days=2), enabled=True, price_state="verified_free",
    )
    return replace(baseline, **changes)


def candidate(platform="zhihu", **changes):
    baseline = {
        "platform": platform, "source_id": "public-001",
        "title": "虚构的公开标题",
        "captured_at": "2026-10-12T12:00:00+00:00",
        "canonical_url": "https://www.zhihu.com/question/123?tracking=removed#fragment",
        "metrics": {"question_count": 4},
    }
    baseline.update(changes)
    return baseline


class SourceIntakeTest(unittest.TestCase):
    def setUp(self):
        self.authority = SyntheticAuthority()
        self.addCleanup(self.authority.close)

    def accept(self, value, permission):
        # 测试模拟管理员签发；生产端只能校验，不能自行签发。
        if isinstance(permission, SourceGrant):
            self.authority.grant(permission)
        return accept_candidate(value, permission, now=NOW)

    def test_forged_grant_rejected_without_trusted_registration(self):
        fabricated = grant(grant_ref="invented-by-untrusted-caller")
        with self.assertRaisesRegex(SourceGateError, "untrusted_source_grant"):
            accept_candidate(candidate(), fabricated, now=NOW)

    def test_signed_grant_tampering_and_revocation_rejected(self):
        genuine = grant()
        self.authority.grant(genuine)
        with self.assertRaisesRegex(SourceGateError, "untrusted_source_grant"):
            accept_candidate(candidate(), replace(genuine, pathway="licensed"), now=NOW)
        # 即使有人能修改登记 JSON，没有管理员签名也无法完成撤销后的伪造恢复。
        self.authority.records["grant"][genuine.grant_ref]["payload"]["revoked"] = True
        self.authority._flush("grant")
        with self.assertRaisesRegex(SourceGateError, "untrusted_source_grant"):
            accept_candidate(candidate(), genuine, now=NOW)

    def test_missing_trusted_registry_fails_closed(self):
        genuine = grant()
        self.authority.grant(genuine)
        from unittest.mock import patch
        with patch.dict("os.environ", {"RADAR_TRUSTED_GRANTS_FILE": ""}):
            with self.assertRaisesRegex(SourceGateError, "untrusted_source_grant"):
                accept_candidate(candidate(), genuine, now=NOW)

    def test_all_five_platforms_are_allowlisted_without_claiming_real_api(self):
        urls = {
            "zhihu": "https://www.zhihu.com/question/1",
            "douyin": "https://www.douyin.com/video/1",
            "bilibili": "https://www.bilibili.com/video/BV1",
            "xiaohongshu": "https://www.xiaohongshu.com/explore/1",
            "xianyu": "https://www.goofish.com/item?id=1",
        }
        for platform, url in urls.items():
            with self.subTest(platform=platform):
                result = self.accept(
                    candidate(platform, canonical_url=url), grant(platform))
                self.assertEqual(result["status"], "staged_only")
                self.assertFalse(result["is_production_raw_event"])
                self.assertNotIn("?", result["canonical_url"])

    def test_missing_or_invalid_grant_fails_closed(self):
        # 非 SourceGrant 对象必须受控拒绝，不应抛出 AttributeError。
        for invalid in (None, {}, "grant", 1, False):
            with self.subTest(grant=repr(invalid)), self.assertRaisesRegex(
                SourceGateError, "invalid_source_grant"
            ):
                self.accept(candidate(), invalid)

    def test_malformed_review_clock_fails_closed(self):
        # 非 datetime 的审查时间必须受控拒绝，不能触发 AttributeError。
        for clock in ("invalid", 0, False, [], {}):
            with self.subTest(clock=repr(clock)), self.assertRaisesRegex(
                SourceGateError, "current_time_must_have_timezone"
            ):
                accept_candidate(candidate(), grant(), now=clock)

    def test_extreme_metric_integer_fails_closed(self):
        # 极大整数转浮点会 OverflowError，必须转为受控的输入拒绝。
        with self.assertRaisesRegex(SourceGateError, "invalid_metric_value"):
            self.accept(candidate(metrics={"question_count": 10 ** 1000}), grant())

    def test_no_authorization_and_expired_grants_rejected(self):
        for permission in [
            grant(enabled=False), grant(grant_ref=""),
            grant(grant_ref=" "), grant(grant_ref="\t"),
            grant(expires_at=NOW - timedelta(seconds=1)),
        ]:
            with self.assertRaises(SourceGateError):
                self.accept(candidate(), permission)

    def test_unknown_cost_does_not_open_api_channel(self):
        for permission in [
            grant(price_state="unknown"),
            grant(price_state="user_approved_paid", user_approved_charge=False),
        ]:
            with self.assertRaisesRegex(SourceGateError, "unapproved_api_cost"):
                self.accept(candidate(), permission)

    def test_paid_authorization_must_be_explicit(self):
        row = self.accept(candidate(),
                          grant(price_state="user_approved_paid",
                                user_approved_charge=True))
        self.assertEqual(row["status"], "staged_only")

    def test_android_screen_requires_user_grant_and_remains_staging(self):
        permission = grant("xiaohongshu", "android_screen", price_state="unknown")
        result = self.accept(
            candidate("xiaohongshu", canonical_url=None, metrics={}), permission)
        self.assertEqual(result["pathway"], "android_screen")
        self.assertFalse(result["is_production_raw_event"])
        with self.assertRaises(SourceGateError):
            self.accept(candidate("xiaohongshu", canonical_url=None),
                        grant("xiaohongshu", "android_screen", enabled=False))

    def test_safety_rejects_unapproved_raw_fields(self):
        for field in ("cookie", "access_token", "device_id", "raw_html", "private_message"):
            with self.subTest(field=field), self.assertRaisesRegex(SourceGateError, "unexpected_fields"):
                self.accept(candidate(**{field: "DO_NOT_STORE"}), grant())

    def test_url_query_credentials_and_unrelated_domain_are_not_stored(self):
        row = self.accept(candidate(), grant())
        self.assertEqual(row["canonical_url"], "https://www.zhihu.com/question/123")
        self.assertNotIn("tracking", str(row))
        for url in ("http://www.zhihu.com/q", "https://evil.example.com/a",
                    "https://" + "user:pass@" + "www.zhihu.com/q"):
            with self.subTest(url=url), self.assertRaises(SourceGateError):
                self.accept(candidate(canonical_url=url), grant())

    def test_duplicate_identity_is_stable_and_platform_scoped(self):
        a = self.accept(candidate(), grant())
        b = self.accept(candidate(metrics={"question_count": 9}), grant())
        c = self.accept(candidate("douyin",
                                  canonical_url="https://www.douyin.com/video/1"),
                        grant("douyin"))
        self.assertEqual(a["dedupe_key"], b["dedupe_key"])
        self.assertNotEqual(a["dedupe_key"], c["dedupe_key"])
        self.assertNotEqual(a["evidence_sha256"], b["evidence_sha256"])

    def test_invalid_metric_values_and_names_rejected(self):
        for metrics in ({"question_count": float("nan")},
                        {"question_count": -1},
                        {"question_count": True},
                        {"followers_token": 1}):
            with self.assertRaises(SourceGateError):
                self.accept(candidate(metrics=metrics), grant())

    def test_invalid_timestamps_cannot_pass(self):
        for timestamp in ("2026-10-12T12:00:00", "bad",
                          "2026-10-15T12:00:00+00:00"):
            with self.assertRaises(SourceGateError):
                self.accept(candidate(captured_at=timestamp), grant())

    def test_unknown_platform_or_wrong_grant_rejected(self):
        with self.assertRaises(SourceGateError):
            self.accept(candidate("unknown"), grant("unknown"))
        with self.assertRaises(SourceGateError):
            self.accept(candidate("zhihu"), grant("douyin"))

    def test_malformed_platform_is_fail_closed_without_type_error(self):
        for item in ([], {}, 123, None):
            with self.subTest(item=repr(item)), self.assertRaises(SourceGateError):
                self.accept(candidate(platform=item), grant())

    def test_false_or_invalid_metrics_cannot_silently_convert_to_empty(self):
        for metrics in (False, 0, [], "", "metrics"):
            with self.subTest(metrics=repr(metrics)), self.assertRaises(SourceGateError):
                self.accept(candidate(metrics=metrics), grant())

    def test_authorization_flag_requires_actual_boolean_true(self):
        for fake in (1, "true", "1", [], None):
            with self.subTest(fake=repr(fake)), self.assertRaises(SourceGateError):
                self.accept(candidate(), grant(enabled=fake))
        with self.assertRaises(SourceGateError):
            self.accept(candidate(), grant(price_state="user_approved_paid",
                                           user_approved_charge=1))

    def test_invalid_expiry_or_grant_reference_is_fail_closed(self):
        for expiry in ("tomorrow", None, 0):
            with self.subTest(expiry=repr(expiry)), self.assertRaises(SourceGateError):
                self.accept(candidate(), grant(expires_at=expiry))
        for ref in (["invalid"], 123, None):
            with self.subTest(ref=repr(ref)), self.assertRaises(SourceGateError):
                self.accept(candidate(), grant(grant_ref=ref))


if __name__ == "__main__":
    unittest.main()
