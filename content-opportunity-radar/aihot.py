"""Experimental AIHOT seed provider.

AIHOT is intentionally opt-in. It supplies candidate-discovery evidence; the
existing deep-scan and Opportunity Score remain authoritative.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from core import (
    AcquisitionMethod,
    CollectionRequest,
    CollectionResult,
    DataProvider,
    ProviderState,
    Provenance,
    RawEvent,
    stable_id,
    utcnow,
)

AIHOT_BASE_URL = "https://aihot.news"
AIHOT_USER_AGENT = "ContentOpportunityRadar/0.1 experimental-aihot-seed"


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class AIHOTProvider(DataProvider):
    id = "aihot"
    source = "aihot.news"
    primary = "AIHOT anonymous read-only v1 API"
    fallback = "disabled; experimental seed source only"
    terms_class = "experimental_personal_research"
    staleness_ttl_seconds = 3600
    retry_attempts = 1

    def collect(self, request: CollectionRequest) -> CollectionResult:
        metadata = request.metadata or {}
        params = {
            "mode": str(metadata.get("mode") or "selected"),
            "window": str(metadata.get("window") or "7d"),
            "by": str(metadata.get("by") or "timeline"),
            "limit": min(max(int(request.limit), 1), 100),
        }
        query = str(metadata.get("q") or request.topic or "").strip()
        if query:
            params["q"] = query
        category = str(metadata.get("category") or "").strip()
        if category:
            params["category"] = category

        endpoint = "/api/v1/items"
        url = f"{AIHOT_BASE_URL}{endpoint}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": AIHOT_USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("AIHOT response larger than 2 MiB")
                payload = json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            state = ProviderState.RATE_LIMITED if exc.code == 429 else ProviderState.FAILED
            return CollectionResult(
                provider=self.id,
                status=state,
                warnings=[f"AIHOT HTTP {exc.code}: {exc.reason}"],
            )
        except Exception as exc:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.FAILED,
                warnings=[f"{type(exc).__name__}: {exc}"],
            )

        now = utcnow()
        events: list[RawEvent] = []
        for item in (payload.get("items") or [])[: request.limit]:
            links = item.get("links") or {}
            source = item.get("source") or {}
            external_id = str(item.get("id") or links.get("aihot") or links.get("original") or "")
            if not external_id:
                continue
            score = item.get("score")
            metrics = {"aihot_score": float(score)} if isinstance(score, (int, float)) else {}
            events.append(
                RawEvent(
                    id=stable_id(self.id, external_id),
                    provider=self.id,
                    source=self.source,
                    acquisition_method=AcquisitionMethod.PUBLIC_WEB_API,
                    retrieved_at=now,
                    external_id=external_id,
                    url=links.get("original") or links.get("aihot"),
                    title=item.get("title") or item.get("originalTitle"),
                    text=item.get("summary") or item.get("reason") or "",
                    author=source.get("name"),
                    community=item.get("category") or "aihot",
                    published_at=_dt(item.get("publishedAt")) or _dt(item.get("discoveredAt")),
                    language="zh",
                    metrics=metrics,
                    raw={
                        "experimental": True,
                        "selected": bool(item.get("selected")),
                        "category": item.get("category"),
                        "reason": item.get("reason"),
                        "source_name": source.get("name"),
                        "aihot_url": links.get("aihot"),
                        "discovered_at": item.get("discoveredAt"),
                    },
                    provenance=Provenance(
                        terms_class=self.terms_class,
                        api_version="v1",
                        endpoint=endpoint,
                    ),
                )
            )
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY if events else ProviderState.DEGRADED,
            events=events,
            warnings=[] if events else ["AIHOT returned no matching items"],
        )
