"""合成数据离线验收；不调用任何真实手机或平台。"""
import unittest
from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from android_staging import ScreenConsent, ScreenDigest, stage_screen, verify_screen_staging
from source_intake import SourceGateError, SourceGrant
from source_promotion import ReviewApproval, preview_raw_event
from test_authority_fixtures import SyntheticAuthority

NOW = datetime(2026, 10, 13, 6, tzinfo=timezone.utc)
CAPTURE = NOW - timedelta(minutes=3)


def fixtures(authority):
    grant = SourceGrant("xiaohongshu", "android_screen", "test-grant",
                        NOW + timedelta(hours=1), True)
    authority.grant(grant)
    consent = ScreenConsent("test-consent", grant.grant_ref, grant.platform,
                            NOW - timedelta(hours=1), NOW + timedelta(minutes=30),
                            "user_visible_foreground_page", True)
    digest = ScreenDigest("test-evidence", grant.platform, CAPTURE, "a" * 64,
                          True, True, True, True)
    candidate = {"platform": grant.platform, "source_id": "synthetic-001",
                 "title": "合成示例", "captured_at": CAPTURE.isoformat(),
                 "canonical_url": "https://www.xiaohongshu.com/explore/1?tracking=discard",
                 "metrics": {}}
    return candidate, grant, consent, digest


class AndroidStagingTests(unittest.TestCase):
    def setUp(self):
        self.authority = SyntheticAuthority()
        self.addCleanup(self.authority.close)

    def test_metadata_only_and_not_production(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        staged = stage_screen(candidate, grant, consent, digest, now=NOW)
        self.assertEqual(staged["status"], "staged_only")
        self.assertFalse(staged["is_production_raw_event"])
        self.assertFalse(staged["persistence_allowed"])
        self.assertEqual(staged["screen_provenance"]["redacted_sha256"], "a" * 64)
        self.assertNotIn("tracking", str(staged))
        self.assertNotIn("image_bytes", staged)
        review = ReviewApproval("test-review", grant.grant_ref,
                                staged["evidence_sha256"], "authorized_public",
                                NOW + timedelta(hours=1), True)
        with self.assertRaises(SourceGateError):
            preview_raw_event(staged, grant, review, now=NOW)

    def test_missing_or_revoked_content_consent_rejected(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        for invalid in (replace(consent, revoked=True), replace(consent, approved=False),
                        replace(consent, approved=1), replace(consent, grant_ref="wrong"),
                        replace(consent, scope="adb_only"), replace(consent, expires_at=NOW)):
            with self.subTest(invalid=invalid), self.assertRaises(SourceGateError):
                stage_screen(candidate, grant, invalid, digest, now=NOW)

    def test_unredacted_or_unreviewed_image_rejected(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        for invalid in (replace(digest, cropped=False), replace(digest, redacted=False),
                        replace(digest, human_reviewed=False),
                        replace(digest, foreground_confirmed=False),
                        replace(digest, original_retained=True),
                        replace(digest, redacted_sha256="bad"),
                        replace(digest, evidence_ref="../outside")):
            with self.subTest(invalid=invalid), self.assertRaises(SourceGateError):
                stage_screen(candidate, grant, consent, invalid, now=NOW)

    def test_capture_time_and_sensitive_fields_rejected(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        with self.assertRaises(SourceGateError):
            stage_screen({**candidate, "captured_at": NOW.isoformat()}, grant, consent, digest, now=NOW)
        with self.assertRaises(SourceGateError):
            stage_screen(candidate, grant, consent,
                         replace(digest, captured_at=NOW - timedelta(hours=3)), now=NOW)
        with self.assertRaises(SourceGateError):
            stage_screen({**candidate, "access_token": "blocked"}, grant, consent, digest, now=NOW)

    def test_future_screen_evidence_is_never_accepted(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        for seconds in (1, 30, 299):
            future = NOW + timedelta(seconds=seconds)
            with self.subTest(seconds=seconds), self.assertRaisesRegex(
                    SourceGateError, "screen_capture_outside_consent"):
                stage_screen({**candidate, "captured_at": future.isoformat()},
                             grant, consent, replace(digest, captured_at=future), now=NOW)

    def test_screen_provenance_is_bound_to_staged_record(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        first = stage_screen(candidate, grant, consent, digest, now=NOW)
        second = stage_screen({**candidate, "source_id": "synthetic-002"},
                              grant, consent, digest, now=NOW)
        self.assertNotEqual(first["evidence_sha256"], second["evidence_sha256"])
        self.assertNotEqual(first["screen_provenance_sha256"],
                            second["screen_provenance_sha256"])
        for row in (first, second):
            proof = row["screen_provenance"]
            self.assertEqual(proof["source_id"], row["source_id"])
            self.assertEqual(proof["captured_at"], row["captured_at"])
            self.assertEqual(proof["staged_evidence_sha256"], row["evidence_sha256"])
            self.assertEqual(proof["grant_ref"], row["grant_ref"])

    def test_independent_screen_provenance_verification(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        staged = stage_screen(candidate, grant, consent, digest, now=NOW)
        trusted = staged["screen_provenance_sha256"]  # 模拟独立保存的可信哈希
        self.assertTrue(verify_screen_staging(
            staged, grant, consent, digest, trusted_provenance_sha256=trusted, now=NOW))
        for change in ({"source_id": "altered"}, {"status": "production_ready"},
                       {"screen_provenance_sha256": "b" * 64},
                       {"persistence_allowed": True}):
            with self.subTest(change=change), self.assertRaises(SourceGateError):
                verify_screen_staging({**staged, **change}, grant, consent, digest,
                                      trusted_provenance_sha256=trusted, now=NOW)
        tampered = deepcopy(staged)
        tampered["screen_provenance"]["source_id"] = "altered"
        with self.assertRaises(SourceGateError):
            verify_screen_staging(tampered, grant, consent, digest,
                                  trusted_provenance_sha256=trusted, now=NOW)

    def test_independent_verification_rechecks_consent_and_trusted_hash(self):
        candidate, grant, consent, digest = fixtures(self.authority)
        staged = stage_screen(candidate, grant, consent, digest, now=NOW)
        trusted = staged["screen_provenance_sha256"]
        invalid_cases = (
            (staged, grant, replace(consent, revoked=True), digest, trusted, NOW),
            (staged, grant, consent, replace(digest, redacted_sha256="b" * 64), trusted, NOW),
            (staged, grant, consent, digest, "b" * 64, NOW),
            (staged, grant, consent, digest, None, NOW),
            (staged, grant, consent, digest, trusted, NOW + timedelta(hours=1)),
        )
        for row, g, c, d, proof, clock in invalid_cases:
            with self.subTest(proof=repr(proof), clock=clock), self.assertRaises(SourceGateError):
                verify_screen_staging(row, g, c, d,
                                      trusted_provenance_sha256=proof, now=clock)


if __name__ == "__main__":
    unittest.main()
