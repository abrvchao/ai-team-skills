"""合成数据离线测试；不调用平台 API、模型或设备。"""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from source_intake import SourceGateError, SourceGrant, accept_candidate
from source_promotion import ReviewApproval, preview_raw_event
from test_authority_fixtures import SyntheticAuthority

NOW = datetime(2026, 10, 13, 4, 15, tzinfo=timezone.utc)


def fixtures(authority, platform="zhihu", pathway="official_api"):
    grant = SourceGrant(platform, pathway, "test-grant", NOW + timedelta(days=1),
                        True, "verified_free")
    authority.grant(grant)
    staged = accept_candidate({
        "platform": platform, "source_id": "synthetic-001",
        "title": "虚构标题", "captured_at": "2026-10-13T04:00:00+00:00",
        "canonical_url": "https://www.zhihu.com/question/1"
                         if platform == "zhihu" else None,
        "metrics": {"question_count": 3},
    }, grant, now=NOW)
    review = ReviewApproval("review-001", grant.grant_ref,
                            staged["evidence_sha256"], "authorized_public",
                            NOW + timedelta(hours=1), True)
    authority.review(review)
    return grant, staged, review


class SourcePromotionTests(unittest.TestCase):
    def setUp(self):
        self.authority = SyntheticAuthority()
        self.addCleanup(self.authority.close)

    def test_forged_review_is_rejected(self):
        grant, staged, review = fixtures(self.authority)
        with self.assertRaisesRegex(SourceGateError, "untrusted_review_approval"):
            preview_raw_event(staged, grant, replace(review, review_ref="forged-review"), now=NOW)

    def test_trusted_review_cannot_be_reused_for_other_evidence(self):
        grant, staged, review = fixtures(self.authority)
        with self.assertRaises(SourceGateError):
            preview_raw_event({**staged, "evidence_sha256": "b" * 64}, grant, review, now=NOW)
    def test_reviewed_memory_preview_is_not_production(self):
        grant, staged, review = fixtures(self.authority)
        result = preview_raw_event(staged, grant, review, now=NOW)
        self.assertEqual(result.status, "offline_preview_only")
        self.assertFalse(result.persistence_allowed)
        self.assertEqual(result.event.provider, "zhihu")
        self.assertIsNone(result.event.raw)

    def test_android_cannot_be_promoted(self):
        grant, staged, review = fixtures(self.authority, "xiaohongshu", "android_screen")
        with self.assertRaises(SourceGateError):
            preview_raw_event(staged, grant, review, now=NOW)

    def test_expired_grant_or_unapproved_review_rejected(self):
        grant, staged, review = fixtures(self.authority)
        for bad_grant in (replace(grant, enabled=False),
                          replace(grant, price_state="unknown")):
            with self.assertRaises(SourceGateError):
                preview_raw_event(staged, bad_grant, review, now=NOW)
        for bad_review in (replace(review, approved=False),
                           replace(review, evidence_sha256="invalid")):
            with self.assertRaises(SourceGateError):
                preview_raw_event(staged, grant, bad_review, now=NOW)

    def test_tampered_staging_rejected(self):
        grant, staged, review = fixtures(self.authority)
        for change in ({"title": "changed"}, {"status": "production_ready"},
                       {"unreviewed_field": "rejected"}):
            with self.assertRaises(SourceGateError):
                preview_raw_event({**staged, **change}, grant, review, now=NOW)

    def test_malformed_review_terms_fail_closed(self):
        grant, staged, review = fixtures(self.authority)
        for terms in ([], {}, None, 123, "unknown"):
            with self.subTest(terms=repr(terms)), self.assertRaises(SourceGateError):
                preview_raw_event(staged, grant, replace(review, terms_class=terms), now=NOW)

    def test_blank_review_reference_is_rejected(self):
        grant, staged, review = fixtures(self.authority)
        for reference in (" ", "\t", "  \t  "):
            with self.subTest(reference=repr(reference)), self.assertRaises(SourceGateError):
                preview_raw_event(staged, grant, replace(review, review_ref=reference), now=NOW)

    def test_malformed_pathway_or_clock_fails_closed(self):
        grant, staged, review = fixtures(self.authority)
        for pathway in ([], {}, None, 1):
            with self.subTest(pathway=repr(pathway)), self.assertRaises(SourceGateError):
                preview_raw_event(staged, replace(grant, pathway=pathway), review, now=NOW)
        for clock in ("now", 1, [], {}):
            with self.subTest(clock=repr(clock)), self.assertRaises(SourceGateError):
                preview_raw_event(staged, grant, review, now=clock)


if __name__ == "__main__":
    unittest.main()
