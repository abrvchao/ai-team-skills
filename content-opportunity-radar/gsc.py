"""Google Search Console provider and first-party opportunity features.

Official API:
POST https://www.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query

Search Console Search Analytics is intentionally treated as first-party evidence,
not as a complete census of search demand: Google documents that the API is
subject to internal limits and returns top rows rather than guaranteeing every
row.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

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


GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
PACIFIC = ZoneInfo("America/Los_Angeles")
Transport = Callable[[str, Mapping[str, str], Mapping[str, Any]], dict[str, Any]]


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=dict(headers),
    )
    with urllib.request.urlopen(request, timeout=20.0) as response:
        raw = response.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("GSC response larger than 10 MiB")
        return json.loads(raw.decode("utf-8", errors="replace"))


def _parse_date(value: str) -> datetime:
    local = datetime.combine(date.fromisoformat(value), time.min, tzinfo=PACIFIC)
    return local.astimezone(timezone.utc)


def _iso_date(value: date | datetime | str) -> str:
    if isinstance(value, str):
        return date.fromisoformat(value).isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def _weighted_position(rows: Sequence[RawEvent]) -> float | None:
    weighted = 0.0
    weight = 0.0
    fallback: list[float] = []
    for row in rows:
        position = row.metrics.get("position")
        if position is None:
            continue
        impressions = max(0.0, row.metrics.get("impressions", 0.0))
        fallback.append(float(position))
        if impressions > 0:
            weighted += float(position) * impressions
            weight += impressions
    if weight > 0:
        return weighted / weight
    if fallback:
        return sum(fallback) / len(fallback)
    return None


def _authority_score(position: float | None) -> float:
    if position is None:
        return 0.0
    if position <= 3:
        return 95.0
    if position <= 10:
        return 85.0
    if position <= 20:
        return 70.0
    if position <= 40:
        return 55.0
    if position <= 60:
        return 35.0
    return 20.0


class GSCProvider(DataProvider):
    id = "gsc"
    source = "searchconsole.googleapis.com"
    primary = "Google Search Console Search Analytics API"
    fallback = "persistent stale provider cache"
    terms_class = "first_party_oauth_top_rows"
    staleness_ttl_seconds = 86400
    retry_attempts = 2

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or _default_transport

    def collect(self, request: CollectionRequest) -> CollectionResult:
        site_url = str(request.metadata.get("site_url") or "").strip()
        if not site_url:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.DISABLED,
                warnings=["request.metadata.site_url is required"],
            )

        access_token = str(
            request.metadata.get("access_token")
            or os.getenv("GSC_ACCESS_TOKEN")
            or ""
        ).strip()
        if not access_token:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.AUTH_REQUIRED,
                warnings=[f"OAuth token required; request scope {GSC_SCOPE}"],
            )

        dimensions = list(request.metadata.get("dimensions") or ["date", "query", "page"])
        allowed = {"date", "query", "page", "country", "device"}
        if not dimensions or any(item not in allowed for item in dimensions):
            return CollectionResult(
                provider=self.id,
                status=ProviderState.FAILED,
                warnings=[f"unsupported GSC dimensions: {dimensions}"],
            )

        today = datetime.now(PACIFIC).date()
        end = date.fromisoformat(
            _iso_date(request.metadata.get("end_date") or (today - timedelta(days=1)))
        )
        start = date.fromisoformat(
            _iso_date(request.metadata.get("start_date") or (end - timedelta(days=34)))
        )
        if start > end:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.FAILED,
                warnings=["start_date must be <= end_date"],
            )

        row_limit = max(1, min(int(request.metadata.get("row_limit") or 25000), 25000))
        max_rows = max(
            row_limit,
            min(int(request.metadata.get("max_rows") or 50000), 100000),
        )
        data_state = str(request.metadata.get("data_state") or "final")
        query_filter = str(request.metadata.get("query_filter") or "").strip()

        encoded_site = urllib.parse.quote(site_url, safe="")
        endpoint = GSC_ENDPOINT.format(site_url=encoded_site)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ContentOpportunityRadar/0.2",
        }

        base_payload: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dimensions,
            "type": "web",
            "dataState": data_state,
            "rowLimit": row_limit,
        }
        if query_filter:
            base_payload["dimensionFilterGroups"] = [{
                "groupType": "and",
                "filters": [{
                    "dimension": "query",
                    "operator": "contains",
                    "expression": query_filter,
                }],
            }]

        events: list[RawEvent] = []
        warnings = [
            "Search Console Search Analytics returns top rows subject to internal limits; results are not exhaustive."
        ]
        start_row = 0
        now = utcnow()

        while start_row < max_rows:
            payload = dict(base_payload)
            payload["startRow"] = start_row
            payload["rowLimit"] = min(row_limit, max_rows - start_row)

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
                    warnings=[*warnings, f"GSC HTTP {exc.code}: {exc.reason}"],
                )
            except Exception as exc:
                return CollectionResult(
                    provider=self.id,
                    status=ProviderState.FAILED,
                    warnings=[*warnings, f"{type(exc).__name__}: {exc}"],
                )

            rows = response.get("rows") or []
            for row in rows:
                keys = list(row.get("keys") or [])
                dimension_values = {
                    name: str(keys[index])
                    for index, name in enumerate(dimensions)
                    if index < len(keys)
                }
                query = dimension_values.get("query") or ""
                page = dimension_values.get("page")
                observed_date = dimension_values.get("date")
                published_at = _parse_date(observed_date) if observed_date else None

                events.append(
                    RawEvent(
                        id=stable_id(
                            self.id,
                            site_url,
                            observed_date,
                            query,
                            page,
                            dimension_values.get("country"),
                            dimension_values.get("device"),
                        ),
                        provider=self.id,
                        source=site_url,
                        acquisition_method=AcquisitionMethod.OAUTH_API,
                        external_id=stable_id(site_url, observed_date, query, page),
                        url=page,
                        title=query or page or site_url,
                        text=query or None,
                        community=site_url,
                        published_at=published_at,
                        retrieved_at=now,
                        country=dimension_values.get("country"),
                        metrics={
                            "clicks": float(row.get("clicks") or 0.0),
                            "impressions": float(row.get("impressions") or 0.0),
                            "ctr": float(row.get("ctr") or 0.0),
                            "position": float(row.get("position") or 0.0),
                        },
                        raw={
                            "dimensions": dimension_values,
                            "response_aggregation_type": response.get("responseAggregationType"),
                            "data_state_requested": data_state,
                            "completeness": "top_rows_only_not_exhaustive",
                        },
                        provenance=Provenance(
                            terms_class=self.terms_class,
                            api_version="v3",
                            endpoint=GSC_ENDPOINT,
                        ),
                    )
                )

            if len(rows) < payload["rowLimit"]:
                break
            start_row += len(rows)
            if not rows:
                break

        if len(events) >= max_rows:
            warnings.append(f"provider max_rows cap reached: {max_rows}")

        if not events:
            warnings.append("GSC returned no rows for the requested window")

        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=events,
            warnings=warnings,
        )


@dataclass(slots=True)
class GSCQueryFeatures:
    query: str
    pages: list[str]
    recent_impressions_per_day: float
    baseline_impressions_per_day: float
    recent_clicks_per_day: float
    recent_ctr: float
    recent_position: float | None
    baseline_position: float | None
    position_improvement: float
    impression_growth_ratio: float
    demand_score: float
    momentum_score: float
    authority_score: float
    confidence: float
    evidence_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "pages": self.pages,
            "recent_impressions_per_day": round(self.recent_impressions_per_day, 4),
            "baseline_impressions_per_day": round(self.baseline_impressions_per_day, 4),
            "recent_clicks_per_day": round(self.recent_clicks_per_day, 4),
            "recent_ctr": round(self.recent_ctr, 6),
            "recent_position": round(self.recent_position, 4) if self.recent_position is not None else None,
            "baseline_position": round(self.baseline_position, 4) if self.baseline_position is not None else None,
            "position_improvement": round(self.position_improvement, 4),
            "impression_growth_ratio": round(self.impression_growth_ratio, 4),
            "demand_score": round(self.demand_score, 2),
            "momentum_score": round(self.momentum_score, 2),
            "authority_score": round(self.authority_score, 2),
            "confidence": round(self.confidence, 2),
            "evidence_ids": self.evidence_ids,
        }


def summarize_gsc_queries(events: Sequence[RawEvent]) -> list[GSCQueryFeatures]:
    gsc_events = [
        event for event in events
        if event.provider == "gsc"
        and isinstance(event.raw, dict)
        and (event.raw.get("dimensions") or {}).get("query")
        and event.published_at is not None
    ]
    if not gsc_events:
        return []

    latest_day = max(event.published_at.date() for event in gsc_events)
    recent_start = latest_day - timedelta(days=6)
    baseline_start = latest_day - timedelta(days=34)
    baseline_end = recent_start - timedelta(days=1)

    by_query: dict[str, list[RawEvent]] = {}
    for event in gsc_events:
        query = str((event.raw.get("dimensions") or {}).get("query") or "").strip()
        if query:
            by_query.setdefault(query, []).append(event)

    output: list[GSCQueryFeatures] = []
    for query, rows in by_query.items():
        recent = [
            row for row in rows
            if recent_start <= row.published_at.date() <= latest_day
        ]
        baseline = [
            row for row in rows
            if baseline_start <= row.published_at.date() <= baseline_end
        ]

        recent_impressions = sum(row.metrics.get("impressions", 0.0) for row in recent)
        baseline_impressions = sum(row.metrics.get("impressions", 0.0) for row in baseline)
        recent_clicks = sum(row.metrics.get("clicks", 0.0) for row in recent)

        recent_per_day = recent_impressions / 7.0
        baseline_per_day = baseline_impressions / 28.0
        if baseline_per_day > 0:
            growth = (recent_per_day - baseline_per_day) / baseline_per_day
        elif recent_per_day > 0:
            growth = 3.0
        else:
            growth = 0.0
        growth = max(-1.0, min(growth, 3.0))

        recent_position = _weighted_position(recent)
        baseline_position = _weighted_position(baseline)
        position_improvement = (
            (baseline_position - recent_position)
            if baseline_position is not None and recent_position is not None
            else 0.0
        )
        recent_ctr = recent_clicks / recent_impressions if recent_impressions > 0 else 0.0

        demand_base = 100.0 * (1.0 - math.exp(-recent_per_day / 100.0))
        growth_bonus = 20.0 * max(0.0, min(growth, 1.5)) / 1.5
        demand_score = clamp(0.8 * demand_base + growth_bonus)

        growth_component = 100.0 * (1.0 - math.exp(-max(0.0, growth)))
        position_component = clamp(max(0.0, position_improvement) * 10.0)
        momentum_score = clamp(0.75 * growth_component + 0.25 * position_component)

        authority_score = _authority_score(recent_position)
        distinct_days = len({row.published_at.date() for row in rows})
        confidence = 78.0 if distinct_days >= 28 else 65.0

        pages = sorted({
            str((row.raw.get("dimensions") or {}).get("page"))
            for row in rows
            if (row.raw.get("dimensions") or {}).get("page")
        })
        evidence_ids = sorted({row.id for row in rows})

        output.append(
            GSCQueryFeatures(
                query=query,
                pages=pages,
                recent_impressions_per_day=recent_per_day,
                baseline_impressions_per_day=baseline_per_day,
                recent_clicks_per_day=recent_clicks / 7.0,
                recent_ctr=recent_ctr,
                recent_position=recent_position,
                baseline_position=baseline_position,
                position_improvement=position_improvement,
                impression_growth_ratio=growth,
                demand_score=demand_score,
                momentum_score=momentum_score,
                authority_score=authority_score,
                confidence=confidence,
                evidence_ids=evidence_ids,
            )
        )

    return sorted(
        output,
        key=lambda item: (item.demand_score + item.momentum_score + item.authority_score),
        reverse=True,
    )


def build_gsc_signals(
    events: Sequence[RawEvent],
    *,
    topic_id: str,
    query_contains: str | None = None,
) -> list[Signal]:
    now = utcnow()
    match = (query_contains or "").casefold().strip()
    signals: list[Signal] = []

    for feature in summarize_gsc_queries(events):
        if match and match not in feature.query.casefold():
            continue

        related = [event for event in events if event.id in set(feature.evidence_ids)]
        source = related[0].source if related else "gsc"
        observed_at = max(
            (event.published_at or event.retrieved_at for event in related),
            default=now,
        )
        freshness_seconds = max(0, int((now - observed_at).total_seconds()))
        entity_ids = [f"keyword:{stable_id(feature.query)}"]

        signals.extend([
            Signal(
                id=stable_id("gsc-demand", topic_id, feature.query),
                topic_id=topic_id,
                entity_ids=entity_ids,
                signal_type="demand",
                source=source,
                provider="gsc",
                observed_at=observed_at,
                value=feature.recent_impressions_per_day,
                normalized_value=feature.demand_score,
                confidence=feature.confidence,
                evidence_ids=feature.evidence_ids,
                freshness_seconds=freshness_seconds,
            ),
            Signal(
                id=stable_id("gsc-momentum", topic_id, feature.query),
                topic_id=topic_id,
                entity_ids=entity_ids,
                signal_type="momentum",
                source=source,
                provider="gsc",
                observed_at=observed_at,
                value=feature.impression_growth_ratio,
                normalized_value=feature.momentum_score,
                confidence=feature.confidence,
                evidence_ids=feature.evidence_ids,
                freshness_seconds=freshness_seconds,
                baseline=feature.baseline_impressions_per_day,
                delta=feature.recent_impressions_per_day - feature.baseline_impressions_per_day,
            ),
            Signal(
                id=stable_id("gsc-authority", topic_id, feature.query),
                topic_id=topic_id,
                entity_ids=entity_ids,
                signal_type="authority",
                source=source,
                provider="gsc",
                observed_at=observed_at,
                value=feature.recent_position or 0.0,
                normalized_value=feature.authority_score,
                confidence=feature.confidence,
                evidence_ids=feature.evidence_ids,
                freshness_seconds=freshness_seconds,
            ),
        ])

    return signals
