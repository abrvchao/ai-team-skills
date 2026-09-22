"""Google Ads Keyword Planner historical metrics provider.

Current production design (2026):
- Google Ads API v25 REST endpoint by default.
- OAuth scope: https://www.googleapis.com/auth/adwords
- Google Cloud project access level controls production access.
- KeywordPlanIdeaService requires sufficient API access (Basic/Standard for
  production planning use; Explorer does not expose planning services).
- Historical metrics refresh monthly, so this provider is baseline/commercial
  context, not a real-time trend detector.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from core import (
    AcquisitionMethod,
    CollectionRequest,
    CollectionResult,
    DataProvider,
    ProviderState,
    Provenance,
    RawEvent,
    Signal,
    clamp,
    stable_id,
    utcnow,
)


GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
DEFAULT_API_VERSION = "v25"
DEFAULT_GEO_TARGET = "2840"  # United States
DEFAULT_LANGUAGE = "1000"  # English
Transport = Callable[[str, Mapping[str, str], Mapping[str, Any]], dict[str, Any]]


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=dict(headers),
    )
    with urllib.request.urlopen(request, timeout=25.0) as response:
        raw = response.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Google Ads response larger than 10 MiB")
        return json.loads(raw.decode("utf-8", errors="replace"))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_customer_id(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def _resource(prefix: str, value: str) -> str:
    value = str(value).strip()
    if not value:
        return ""
    if "/" in value:
        return value
    return f"{prefix}/{value}"


def demand_baseline_score(avg_monthly_searches: float) -> float:
    """Log-scaled baseline demand.

    100 searches/month ~= 40, 1k ~= 60, 10k ~= 80, 100k ~= 100.
    This deliberately compresses head terms so B2B/niche opportunities remain
    comparable instead of being drowned by consumer-scale keywords.
    """
    if avg_monthly_searches <= 0:
        return 0.0
    return clamp(math.log10(1.0 + avg_monthly_searches) / 5.0 * 100.0)


def commercial_intent_score(
    *,
    competition_index: float | None,
    competition: str | None,
    high_top_of_page_bid_micros: float,
) -> float:
    competition_map = {
        "LOW": 25.0,
        "MEDIUM": 55.0,
        "HIGH": 85.0,
    }
    competition_score = (
        clamp(competition_index)
        if competition_index is not None
        else competition_map.get(str(competition or "").upper(), 0.0)
    )

    bid_dollars = max(0.0, high_top_of_page_bid_micros) / 1_000_000.0
    # $1 ~= 15, $10 ~= 52, $100 ~= 100.
    bid_score = clamp(math.log10(1.0 + bid_dollars) / 2.0 * 100.0)
    return clamp(0.45 * competition_score + 0.55 * bid_score)


class KeywordPlannerProvider(DataProvider):
    id = "keyword_planner"
    source = "googleads.googleapis.com"
    primary = "Google Ads API KeywordPlanIdeaService.GenerateKeywordHistoricalMetrics"
    fallback = "persistent stale provider cache"
    terms_class = "official_api_monthly_keyword_research"
    staleness_ttl_seconds = 35 * 86400
    retry_attempts = 2
    retry_backoff_seconds = 1.0

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or _default_transport

    def collect(self, request: CollectionRequest) -> CollectionResult:
        customer_id = _clean_customer_id(
            str(
                request.metadata.get("customer_id")
                or os.getenv("GOOGLE_ADS_CUSTOMER_ID")
                or ""
            )
        )
        if not customer_id:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.DISABLED,
                warnings=["Google Ads customer_id is required"],
            )

        access_token = str(
            request.metadata.get("access_token")
            or os.getenv("GOOGLE_ADS_ACCESS_TOKEN")
            or ""
        ).strip()
        if not access_token:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.AUTH_REQUIRED,
                warnings=[f"OAuth token required with scope {GOOGLE_ADS_SCOPE}"],
            )

        api_version = str(
            request.metadata.get("api_version")
            or os.getenv("GOOGLE_ADS_API_VERSION")
            or DEFAULT_API_VERSION
        ).strip()
        if not re.fullmatch(r"v\d+", api_version):
            return CollectionResult(
                provider=self.id,
                status=ProviderState.FAILED,
                warnings=[f"invalid Google Ads API version: {api_version}"],
            )

        configured_keywords = request.metadata.get("keywords")
        if configured_keywords:
            keywords = [
                str(item).strip()
                for item in configured_keywords
                if str(item).strip()
            ]
        else:
            keywords = [request.topic.strip()] if request.topic.strip() else []

        keywords = list(dict.fromkeys(keywords))[:20]
        if not keywords:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.DISABLED,
                warnings=["at least one keyword is required"],
            )

        geo_target = _resource(
            "geoTargetConstants",
            str(request.metadata.get("geo_target") or DEFAULT_GEO_TARGET),
        )
        language = _resource(
            "languageConstants",
            str(request.metadata.get("language_constant") or DEFAULT_LANGUAGE),
        )

        endpoint = (
            f"https://googleads.googleapis.com/{api_version}/customers/"
            f"{customer_id}:generateKeywordHistoricalMetrics"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ContentOpportunityRadar/0.4",
        }

        login_customer_id = _clean_customer_id(
            str(
                request.metadata.get("login_customer_id")
                or os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
                or ""
            )
        )
        if login_customer_id:
            headers["login-customer-id"] = login_customer_id

        # Developer tokens were sunset as the access-level carrier in Sep 2026.
        # Keep the header optional for compatibility with older client setups.
        legacy_developer_token = str(
            request.metadata.get("developer_token")
            or os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
            or ""
        ).strip()
        if legacy_developer_token:
            headers["developer-token"] = legacy_developer_token

        payload: dict[str, Any] = {
            "keywords": keywords,
            "keywordPlanNetwork": "GOOGLE_SEARCH",
            "historicalMetricsOptions": {
                "includeAverageCpc": True,
            },
        }
        if geo_target:
            payload["geoTargetConstants"] = [geo_target]
        if language:
            payload["language"] = language

        try:
            response = self._transport(endpoint, headers, payload)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                state = ProviderState.AUTH_REQUIRED
            elif exc.code == 429:
                state = ProviderState.RATE_LIMITED
            else:
                state = ProviderState.FAILED
            return CollectionResult(
                provider=self.id,
                status=state,
                warnings=[f"Google Ads HTTP {exc.code}: {exc.reason}"],
            )
        except Exception as exc:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.FAILED,
                warnings=[f"{type(exc).__name__}: {exc}"],
            )

        now = utcnow()
        events: list[RawEvent] = []
        for item in response.get("results") or []:
            text = str(item.get("text") or "").strip()
            metrics = item.get("keywordMetrics") or {}
            if not text:
                continue

            avg_monthly = _float(metrics.get("avgMonthlySearches"))
            competition_index_raw = metrics.get("competitionIndex")
            competition_index = (
                _float(competition_index_raw)
                if competition_index_raw is not None
                else None
            )
            low_bid = _float(metrics.get("lowTopOfPageBidMicros"))
            high_bid = _float(metrics.get("highTopOfPageBidMicros"))
            avg_cpc = _float(metrics.get("averageCpcMicros"))

            event_metrics = {
                "avg_monthly_searches": avg_monthly,
                "low_top_of_page_bid_micros": low_bid,
                "high_top_of_page_bid_micros": high_bid,
            }
            if competition_index is not None:
                event_metrics["competition_index"] = competition_index
            if avg_cpc > 0:
                event_metrics["average_cpc_micros"] = avg_cpc

            events.append(
                RawEvent(
                    id=stable_id(
                        self.id,
                        customer_id,
                        text.casefold(),
                        geo_target,
                        language,
                    ),
                    provider=self.id,
                    source=self.source,
                    acquisition_method=AcquisitionMethod.OAUTH_API,
                    external_id=text.casefold(),
                    url=None,
                    title=text,
                    text=text,
                    published_at=None,
                    retrieved_at=now,
                    language=language,
                    country=geo_target,
                    metrics=event_metrics,
                    raw={
                        "competition": metrics.get("competition"),
                        "monthly_search_volumes": metrics.get("monthlySearchVolumes") or [],
                        "close_variants": item.get("closeVariants") or [],
                        "api_version": api_version,
                    },
                    provenance=Provenance(
                        terms_class=self.terms_class,
                        api_version=api_version,
                        endpoint=(
                            "KeywordPlanIdeaService."
                            "GenerateKeywordHistoricalMetrics"
                        ),
                    ),
                )
            )

        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY if events else ProviderState.DEGRADED,
            events=events,
            warnings=(
                [
                    "Historical keyword metrics refresh monthly; use as baseline/commercial context, not real-time momentum."
                ]
                if events
                else ["Google Ads returned no historical keyword metrics"]
            ),
        )


def _matches_terms(text: str, query_terms: Sequence[str]) -> bool:
    normalized = text.casefold().strip()
    if not query_terms:
        return True
    return any(term.casefold().strip() in normalized for term in query_terms if term.strip())


def aggregate_keyword_planner_signals(
    events: Sequence[RawEvent],
    *,
    topic_id: str,
    query_terms: Sequence[str] = (),
) -> list[Signal]:
    rows = [
        event
        for event in events
        if event.provider == KeywordPlannerProvider.id
        and _matches_terms(event.title or "", query_terms)
    ]
    if not rows:
        return []

    avg_searches = sum(
        event.metrics.get("avg_monthly_searches", 0.0)
        for event in rows
    ) / len(rows)

    competition_values = [
        event.metrics["competition_index"]
        for event in rows
        if "competition_index" in event.metrics
    ]
    competition_index = (
        sum(competition_values) / len(competition_values)
        if competition_values
        else None
    )
    high_bid = max(
        (event.metrics.get("high_top_of_page_bid_micros", 0.0) for event in rows),
        default=0.0,
    )
    competition_labels = [
        str((event.raw or {}).get("competition") or "")
        for event in rows
        if isinstance(event.raw, dict)
    ]
    competition = next((item for item in competition_labels if item), None)

    demand_score = demand_baseline_score(avg_searches)
    commercial_score = commercial_intent_score(
        competition_index=competition_index,
        competition=competition,
        high_top_of_page_bid_micros=high_bid,
    )
    observed_at = max(event.retrieved_at for event in rows)
    evidence_ids = sorted({event.id for event in rows})
    source = rows[0].source

    demand = Signal(
        id=stable_id("keyword-planner-demand", topic_id, *evidence_ids),
        topic_id=topic_id,
        entity_ids=[],
        signal_type="demand",
        source=source,
        provider=KeywordPlannerProvider.id,
        observed_at=observed_at,
        value=avg_searches,
        normalized_value=demand_score,
        confidence=86.0,
        evidence_ids=evidence_ids,
        freshness_seconds=0,
    )
    commercial = Signal(
        id=stable_id("keyword-planner-commercial", topic_id, *evidence_ids),
        topic_id=topic_id,
        entity_ids=[],
        signal_type="commercial",
        source=source,
        provider=KeywordPlannerProvider.id,
        observed_at=observed_at,
        value=high_bid / 1_000_000.0,
        normalized_value=commercial_score,
        confidence=82.0,
        evidence_ids=evidence_ids,
        freshness_seconds=0,
    )
    return [demand, commercial]
