"""Only synthetic offline fixtures. No device, API credentials, or platform requests."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from source_intake import SourceGrant, SourceGateError, accept_candidate

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
    def accept(self, value, permission):
        return accept_candidate(value, permission, now=NOW)

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

    def test_no_authorization_and_expired_grants_rejected(self):
        for permission in [
            grant(enabled=False), grant(grant_ref=""),
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
                    "https://user:pass@www.zhihu.com/q"):
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


if __name__ == "__main__":
    unittest.main()
