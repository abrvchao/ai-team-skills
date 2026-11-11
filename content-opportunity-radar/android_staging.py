"""离线安卓屏幕证据摘要契约；不读取设备、截图、网络或数据库。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from collections.abc import Mapping
import json
import re

from source_intake import INPUT_FIELDS, SourceGateError, SourceGrant, accept_candidate

_HASH = re.compile(r"[a-f0-9]{64}\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")


@dataclass(frozen=True)
class ScreenConsent:
    consent_ref: str
    grant_ref: str
    platform: str
    granted_at: datetime
    expires_at: datetime
    scope: str
    approved: bool
    revoked: bool = False


@dataclass(frozen=True)
class ScreenDigest:
    evidence_ref: str
    platform: str
    captured_at: datetime
    redacted_sha256: str
    cropped: bool
    redacted: bool
    human_reviewed: bool
    foreground_confirmed: bool
    original_retained: bool = False


def stage_screen(candidate, grant, consent, digest, *, now=None):
    """只输出不含图像字节的 staging；ADB 授权不等于内容采集授权。"""
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise SourceGateError("invalid_review_clock")
    if not isinstance(grant, SourceGrant) or grant.pathway != "android_screen":
        raise SourceGateError("screen_grant_required")
    if not isinstance(consent, ScreenConsent) or not isinstance(digest, ScreenDigest):
        raise SourceGateError("missing_screen_consent_or_digest")
    grant.validate(current)
    if (consent.approved is not True or consent.revoked is not False
            or consent.scope != "user_visible_foreground_page"
            or consent.grant_ref != grant.grant_ref or consent.platform != grant.platform
            or not isinstance(consent.consent_ref, str) or not _REF.fullmatch(consent.consent_ref)):
        raise SourceGateError("explicit_screen_consent_required")
    if (not isinstance(consent.granted_at, datetime)
            or not isinstance(consent.expires_at, datetime)
            or consent.granted_at.tzinfo is None or consent.expires_at.tzinfo is None
            or not consent.granted_at <= current < consent.expires_at):
        raise SourceGateError("screen_consent_expired")
    if (digest.platform != grant.platform
            or not isinstance(digest.evidence_ref, str) or not _REF.fullmatch(digest.evidence_ref)
            or not isinstance(digest.redacted_sha256, str)
            or not _HASH.fullmatch(digest.redacted_sha256)
            or digest.cropped is not True or digest.redacted is not True
            or digest.human_reviewed is not True or digest.foreground_confirmed is not True
            or digest.original_retained is not False):
        raise SourceGateError("unreviewed_or_unredacted_screen")
    if (not isinstance(digest.captured_at, datetime) or digest.captured_at.tzinfo is None
            or not consent.granted_at <= digest.captured_at < consent.expires_at
            # 屏幕证据必须已经产生；普通 API 的五分钟时钟容差不适用于截图。
            or digest.captured_at > current):
        raise SourceGateError("screen_capture_outside_consent")
    staged = accept_candidate(candidate, grant, now=current)
    if datetime.fromisoformat(staged["captured_at"]) != digest.captured_at:
        raise SourceGateError("screen_candidate_timestamp_mismatch")
    # 将截图证明绑定到唯一的暂存记录，而非仅绑定一张可重复引用的截图。
    # SHA-256 是离线一致性校验，不是设备签名或图片内容真实性证明。
    proof = {"consent_ref": consent.consent_ref, "consent_scope": consent.scope,
             "evidence_ref": digest.evidence_ref, "redacted_sha256": digest.redacted_sha256,
             "platform": staged["platform"], "source_id": staged["source_id"],
             "captured_at": staged["captured_at"], "grant_ref": staged["grant_ref"],
             "staged_evidence_sha256": staged["evidence_sha256"]}
    proof_hash = sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
    return {**staged, "screen_provenance": proof,
            "screen_provenance_sha256": proof_hash,
            "status": "staged_only", "is_production_raw_event": False,
            "persistence_allowed": False}


def verify_screen_staging(staged, grant, consent, digest, *,
                          trusted_provenance_sha256, now=None):
    """独立复核离线证据。可信哈希必须从暂存记录之外的受控位置提供。

    仅验证元数据一致性、当前授权与可信哈希，不证明截图真实性或允许入库。
    """
    if not isinstance(staged, Mapping):
        raise SourceGateError("invalid_screen_staging")
    if (not isinstance(trusted_provenance_sha256, str)
            or not _HASH.fullmatch(trusted_provenance_sha256)):
        raise SourceGateError("missing_trusted_screen_provenance")
    candidate = {key: staged[key] for key in INPUT_FIELDS if key in staged}
    expected = stage_screen(candidate, grant, consent, digest, now=now)
    if dict(staged) != expected:
        raise SourceGateError("screen_staging_modified")
    if not compare_digest(expected["screen_provenance_sha256"],
                          trusted_provenance_sha256):
        raise SourceGateError("screen_provenance_trust_mismatch")
    return True
