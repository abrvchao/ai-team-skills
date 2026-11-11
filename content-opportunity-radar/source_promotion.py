"""离线来源复核：构建 RawEvent 内存预览，不写库、不发网络请求。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from core import AcquisitionMethod, Provenance, RawEvent, stable_id
from source_intake import SourceGateError, SourceGrant, accept_candidate
from source_authority import AuthorityError, require_review
from event_persistence import PREVIEW_SOURCE

PROMOTABLE = frozenset({"official_api", "oauth_api", "licensed"})
TERMS = frozenset({"authorized_public", "authorized_creator", "licensed_content"})
CANDIDATE_FIELDS = ("platform", "source_id", "title", "captured_at", "canonical_url", "metrics")


@dataclass(frozen=True)
class ReviewApproval:
    review_ref: str
    grant_ref: str
    evidence_sha256: str
    terms_class: str
    expires_at: datetime
    approved: bool


@dataclass(frozen=True)
class RawEventPreview:
    event: RawEvent
    review_ref: str
    grant_ref: str
    evidence_sha256: str
    status: str = "offline_preview_only"
    persistence_allowed: bool = False


def preview_raw_event(staged: Mapping, grant: SourceGrant, review: ReviewApproval,
                      *, now: datetime | None = None) -> RawEventPreview:
    """双重授权 + 暂存哈希核验；Android 证据禁止转换为生产事件。"""
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise SourceGateError("timezone_required")
    if (not isinstance(grant, SourceGrant) or not isinstance(grant.pathway, str)
            or grant.pathway not in PROMOTABLE):
        raise SourceGateError("staging_only_android_or_unsupported")
    if not isinstance(staged, Mapping) or not isinstance(review, ReviewApproval):
        raise SourceGateError("missing_review_or_staging")
    if (review.approved is not True or not isinstance(review.review_ref, str)
            or not review.review_ref.strip() or not 1 <= len(review.review_ref) <= 100
            or not isinstance(review.terms_class, str)
            or review.terms_class not in TERMS
            or not isinstance(review.expires_at, datetime)
            or review.expires_at.tzinfo is None or review.expires_at <= current):
        raise SourceGateError("independent_review_invalid")
    # 第二道门禁使用独立于来源授权的审查者密钥/登记表。
    try:
        require_review(review)
    except AuthorityError:
        raise SourceGateError("untrusted_review_approval") from None
    candidate = {key: staged[key] for key in CANDIDATE_FIELDS if key in staged}
    expected = accept_candidate(candidate, grant, now=current)
    if dict(staged) != expected:
        raise SourceGateError("staged_evidence_modified")
    if (review.grant_ref != grant.grant_ref
            or review.evidence_sha256 != expected["evidence_sha256"]):
        raise SourceGateError("review_evidence_binding_invalid")
    event = RawEvent(
        id=stable_id("source-intake-reviewed", expected["dedupe_key"]),
        provider=grant.platform, source=PREVIEW_SOURCE,
        acquisition_method=AcquisitionMethod(grant.pathway),
        retrieved_at=datetime.fromisoformat(expected["captured_at"]),
        external_id=expected["source_id"], url=expected["canonical_url"],
        title=expected["title"], metrics=dict(expected["metrics"]),
        raw=None, provenance=Provenance(terms_class=review.terms_class),
    )
    return RawEventPreview(event, review.review_ref, grant.grant_ref,
                           expected["evidence_sha256"])
