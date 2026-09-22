"""Core contracts for Content Opportunity Radar Phase 1.

The core deliberately separates acquisition from interpretation:
providers collect raw facts; later layers derive signals/features/scores.
"""
from __future__ import annotations

import hashlib
import json
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(p or "") for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class ProviderState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
    DISABLED = "disabled"
    FAILED = "failed"


class AcquisitionMethod(str, Enum):
    OFFICIAL_API = "official_api"
    OAUTH_API = "oauth_api"
    RSS = "rss"
    ATOM = "atom"
    PUBLIC_WEB_API = "public_web_api"
    HTML = "html"
    SEARCH_PROVIDER = "search_provider"
    LICENSED = "licensed"


@dataclass(slots=True)
class Provenance:
    terms_class: str
    api_version: str | None = None
    endpoint: str | None = None


@dataclass(slots=True)
class RawEvent:
    id: str
    provider: str
    source: str
    acquisition_method: AcquisitionMethod
    retrieved_at: datetime
    external_id: str | None = None
    url: str | None = None
    title: str | None = None
    text: str | None = None
    author: str | None = None
    community: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    country: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    raw: Any = None
    provenance: Provenance = field(default_factory=lambda: Provenance("unknown"))

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["acquisition_method"] = self.acquisition_method.value
        data["retrieved_at"] = isoformat(self.retrieved_at)
        data["published_at"] = isoformat(self.published_at)
        if not include_raw:
            data.pop("raw", None)
        return data


@dataclass(slots=True)
class Signal:
    id: str
    topic_id: str
    entity_ids: list[str]
    signal_type: str
    source: str
    provider: str
    observed_at: datetime
    value: float
    normalized_value: float
    confidence: float
    evidence_ids: list[str]
    freshness_seconds: int
    baseline: float | None = None
    delta: float | None = None
    velocity: float | None = None
    acceleration: float | None = None
    geo: str | None = None
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["observed_at"] = isoformat(self.observed_at)
        return data


@dataclass(slots=True)
class MetricSnapshot:
    subject_type: str
    subject_id: str
    metric: str
    value: float
    collected_at: datetime
    provider: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["collected_at"] = isoformat(self.collected_at)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MetricSnapshot":
        timestamp = data["collected_at"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            subject_type=str(data["subject_type"]),
            subject_id=str(data["subject_id"]),
            metric=str(data["metric"]),
            value=float(data["value"]),
            collected_at=timestamp,
            provider=str(data["provider"]),
        )


@dataclass(slots=True)
class CollectionRequest:
    topic: str
    limit: int = 10
    language: str = "en"
    country: str = "US"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RateLimit:
    remaining: int | None = None
    reset_at: datetime | None = None


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    state: ProviderState
    checked_at: datetime = field(default_factory=utcnow)
    message: str = ""


@dataclass(slots=True)
class CollectionResult:
    provider: str
    status: ProviderState
    events: list[RawEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cursor: str | None = None
    rate_limit: RateLimit | None = None
    collected_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "events": [event.to_dict() for event in self.events],
            "warnings": self.warnings,
            "cursor": self.cursor,
            "rate_limit": {
                "remaining": self.rate_limit.remaining,
                "reset_at": isoformat(self.rate_limit.reset_at),
            } if self.rate_limit else None,
            "collected_at": isoformat(self.collected_at),
        }


class DataProvider(ABC):
    """Provider boundary. Source-specific quirks must stay behind this interface."""

    id = "provider"
    source = "unknown"
    primary = ""
    fallback = ""
    terms_class = "public"
    staleness_ttl_seconds = 3600

    @abstractmethod
    def collect(self, request: CollectionRequest) -> CollectionResult:
        raise NotImplementedError

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.id, ProviderState.HEALTHY)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, DataProvider] = {}

    def register(self, provider: DataProvider) -> None:
        if provider.id in self._providers:
            raise ValueError(f"provider already registered: {provider.id}")
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> DataProvider:
        return self._providers[provider_id]

    def providers(self) -> list[DataProvider]:
        return list(self._providers.values())

    def safe_collect(self, provider: DataProvider, request: CollectionRequest) -> CollectionResult:
        try:
            return provider.collect(request)
        except Exception as exc:  # provider failure isolation is intentional
            return CollectionResult(
                provider=provider.id,
                status=ProviderState.FAILED,
                warnings=[f"{type(exc).__name__}: {exc}"],
            )


class SnapshotStore:
    """Append-only time-series snapshot store.

    If a path is provided, every new snapshot is appended as one JSON line.
    Existing history is loaded at startup, so repeated demo runs accumulate
    enough history for velocity/acceleration features.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: list[MetricSnapshot] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._rows.append(MetricSnapshot.from_dict(json.loads(line)))
                except Exception:
                    # Corrupt rows are skipped rather than destroying all history.
                    continue

    def append(self, snapshot: MetricSnapshot) -> None:
        with self._lock:
            self._rows.append(snapshot)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")

    def extend(self, snapshots: Iterable[MetricSnapshot]) -> None:
        for snapshot in snapshots:
            self.append(snapshot)

    def series(
        self,
        subject_type: str,
        subject_id: str,
        metric: str,
        provider: str | None = None,
    ) -> list[MetricSnapshot]:
        rows = [
            row for row in self._rows
            if row.subject_type == subject_type
            and row.subject_id == subject_id
            and row.metric == metric
            and (provider is None or row.provider == provider)
        ]
        return sorted(rows, key=lambda row: row.collected_at)

    def latest(
        self,
        subject_type: str,
        subject_id: str,
        metric: str,
        provider: str | None = None,
    ) -> MetricSnapshot | None:
        rows = self.series(subject_type, subject_id, metric, provider)
        return rows[-1] if rows else None

    def all(self) -> list[MetricSnapshot]:
        return list(self._rows)
