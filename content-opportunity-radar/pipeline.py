"""Phase-1 real-source pipeline for Content Opportunity Radar.

Run:
    python pipeline.py --topic "AI Agents" --limit 10

The pipeline never asks an LLM whether a trend exists. It collects raw evidence,
persists metric snapshots, derives deterministic features, then scores the
opportunity with an explainable formula.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from core import (
    AcquisitionMethod,
    CollectionRequest,
    CollectionResult,
    DataProvider,
    EventCache,
    MetricSnapshot,
    ProviderRegistry,
    ProviderState,
    Provenance,
    RateLimit,
    RawEvent,
    Signal,
    SnapshotStore,
    clamp,
    stable_id,
    utcnow,
)
from entities import TopicResolver, default_resolver, normalize_text
from features import acceleration, summarize_signals, velocity
from opportunity import score_opportunity
from web import WebsiteProvider, fetch_text, parse_feed


USER_AGENT = "ContentOpportunityRadar/0.1 (+https://github.com/abrvchao/ai-team-skills)"


def _json_request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[Any, dict[str, str]]:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("JSON response larger than 5 MiB")
        return json.loads(raw.decode("utf-8", errors="replace")), dict(response.headers.items())


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _reset_at(headers: dict[str, str]) -> datetime | None:
    raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc) if raw else None
    except Exception:
        return None


def _remaining(headers: dict[str, str]) -> int | None:
    raw = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


class GitHubProvider(DataProvider):
    id = "github"
    source = "github.com"
    primary = "GitHub REST API"
    fallback = "stale snapshots only; never scrape GitHub search HTML"
    terms_class = "official_api"
    staleness_ttl_seconds = 21600

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def collect(self, request: CollectionRequest) -> CollectionResult:
        query = request.topic.replace('"', " ").strip()
        repo_q = f'{query} in:name,description'
        issue_q = f'{query} in:title,body is:issue'
        repo_url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
            "q": repo_q,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max(request.limit, 1), 30),
        })
        issue_url = "https://api.github.com/search/issues?" + urllib.parse.urlencode({
            "q": issue_q,
            "sort": "created",
            "order": "desc",
            "per_page": min(max(request.limit, 1), 30),
        })

        try:
            repos, repo_headers = _json_request(repo_url, headers=self._headers())
            issues, issue_headers = _json_request(issue_url, headers=self._headers())
        except urllib.error.HTTPError as exc:
            state = ProviderState.RATE_LIMITED if exc.code in {403, 429} else ProviderState.FAILED
            return CollectionResult(
                provider=self.id,
                status=state,
                warnings=[f"GitHub HTTP {exc.code}: {exc.reason}"],
            )

        now = utcnow()
        events: list[RawEvent] = []

        for item in repos.get("items", [])[: request.limit]:
            repo_name = item.get("full_name") or str(item.get("id"))
            events.append(
                RawEvent(
                    id=stable_id(self.id, "repo", item.get("id")),
                    provider=self.id,
                    source=self.source,
                    acquisition_method=AcquisitionMethod.OFFICIAL_API,
                    external_id=str(item.get("id") or repo_name),
                    url=item.get("html_url"),
                    title=repo_name,
                    text=item.get("description") or "",
                    author=(item.get("owner") or {}).get("login"),
                    community=repo_name,
                    published_at=_dt(item.get("created_at")),
                    retrieved_at=now,
                    language=item.get("language"),
                    metrics={
                        "stars": float(item.get("stargazers_count") or 0),
                        "forks": float(item.get("forks_count") or 0),
                        "open_issues": float(item.get("open_issues_count") or 0),
                    },
                    raw={
                        "kind": "repository",
                        "topics": item.get("topics") or [],
                        "pushed_at": item.get("pushed_at"),
                    },
                    provenance=Provenance(
                        terms_class=self.terms_class,
                        api_version="2022-11-28",
                        endpoint="GET /search/repositories",
                    ),
                )
            )

        for item in issues.get("items", [])[: request.limit]:
            user = item.get("user") or {}
            if user.get("type") == "Bot":
                continue
            repo_url_raw = item.get("repository_url") or ""
            repo_name = repo_url_raw.split("/repos/")[-1] if "/repos/" in repo_url_raw else ""
            events.append(
                RawEvent(
                    id=stable_id(self.id, "issue", item.get("id")),
                    provider=self.id,
                    source=self.source,
                    acquisition_method=AcquisitionMethod.OFFICIAL_API,
                    external_id=str(item.get("id") or item.get("number") or ""),
                    url=item.get("html_url"),
                    title=item.get("title"),
                    text=(item.get("body") or "")[:3000],
                    author=user.get("login"),
                    community=repo_name,
                    published_at=_dt(item.get("created_at")),
                    retrieved_at=now,
                    language="en",
                    metrics={
                        "comments": float(item.get("comments") or 0),
                        "reactions": float((item.get("reactions") or {}).get("total_count") or 0),
                    },
                    raw={"kind": "issue"},
                    provenance=Provenance(
                        terms_class=self.terms_class,
                        api_version="2022-11-28",
                        endpoint="GET /search/issues",
                    ),
                )
            )

        remaining_values = [x for x in (_remaining(repo_headers), _remaining(issue_headers)) if x is not None]
        reset_values = [x for x in (_reset_at(repo_headers), _reset_at(issue_headers)) if x is not None]
        rate = RateLimit(
            remaining=min(remaining_values) if remaining_values else None,
            reset_at=max(reset_values) if reset_values else None,
        )
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY if events else ProviderState.DEGRADED,
            events=events,
            warnings=[] if events else ["GitHub returned no matching repositories/issues"],
            rate_limit=rate,
        )


class HackerNewsProvider(DataProvider):
    id = "hackernews"
    source = "news.ycombinator.com"
    primary = "HN Algolia Search API"
    fallback = "hnrss.org RSS"
    terms_class = "public_api"

    def collect(self, request: CollectionRequest) -> CollectionResult:
        params = urllib.parse.urlencode({
            "tags": "story",
            "query": request.topic,
            "hitsPerPage": min(max(request.limit, 1), 50),
        })
        url = "https://hn.algolia.com/api/v1/search_by_date?" + params
        now = utcnow()
        warnings: list[str] = []
        events: list[RawEvent] = []

        try:
            data, _ = _json_request(url)
            for hit in data.get("hits", [])[: request.limit]:
                object_id = str(hit.get("objectID") or "")
                hn_url = f"https://news.ycombinator.com/item?id={object_id}"
                events.append(
                    RawEvent(
                        id=stable_id(self.id, object_id),
                        provider=self.id,
                        source=self.source,
                        acquisition_method=AcquisitionMethod.PUBLIC_WEB_API,
                        external_id=object_id,
                        url=hit.get("url") or hn_url,
                        title=hit.get("title") or hit.get("story_title"),
                        text=hit.get("story_text") or "",
                        author=hit.get("author"),
                        community="Hacker News",
                        published_at=_dt(hit.get("created_at")),
                        retrieved_at=now,
                        language="en",
                        metrics={
                            "points": float(hit.get("points") or 0),
                            "comments": float(hit.get("num_comments") or 0),
                        },
                        raw={"hn_url": hn_url},
                        provenance=Provenance(
                            terms_class=self.terms_class,
                            endpoint="https://hn.algolia.com/api/v1/search_by_date",
                        ),
                    )
                )
        except Exception as exc:
            warnings.append(f"Algolia failed: {type(exc).__name__}: {exc}")

        if events:
            return CollectionResult(self.id, ProviderState.HEALTHY, events=events, warnings=warnings)

        # RSS is a real fallback, but less expressive and may not expose engagement.
        try:
            rss_url = "https://hnrss.org/newest?" + urllib.parse.urlencode({"q": request.topic, "count": request.limit})
            response = fetch_text(rss_url, accept="application/rss+xml,application/xml,*/*;q=0.5")
            for row in parse_feed(response.text, response.url, request.limit):
                events.append(
                    RawEvent(
                        id=stable_id(self.id, row.get("url"), row.get("title")),
                        provider=self.id,
                        source=self.source,
                        acquisition_method=AcquisitionMethod.RSS,
                        external_id=str(row.get("external_id") or row.get("url") or ""),
                        url=row.get("url"),
                        title=row.get("title"),
                        text=row.get("text"),
                        published_at=row.get("published_at"),
                        retrieved_at=now,
                        language="en",
                        metrics={"points": 0.0, "comments": 0.0},
                        raw={"fallback": True},
                        provenance=Provenance(
                            terms_class=self.terms_class,
                            endpoint=rss_url,
                        ),
                    )
                )
            warnings.append("HN Algolia unavailable/empty; RSS fallback used")
        except Exception as exc:
            warnings.append(f"HN RSS fallback failed: {type(exc).__name__}: {exc}")

        return CollectionResult(
            self.id,
            ProviderState.DEGRADED if events else ProviderState.FAILED,
            events=events,
            warnings=warnings,
        )


class GoogleNewsProvider(DataProvider):
    id = "google_news"
    source = "news.google.com"
    primary = "Google News RSS Search"
    fallback = "none; degrade without blocking other providers"
    terms_class = "rss"

    def collect(self, request: CollectionRequest) -> CollectionResult:
        query = f"{request.topic} when:7d"
        params = urllib.parse.urlencode({
            "q": query,
            "hl": "en-US" if request.language == "en" else request.language,
            "gl": request.country,
            "ceid": f"{request.country}:en" if request.language == "en" else f"{request.country}:{request.language}",
        })
        url = "https://news.google.com/rss/search?" + params
        now = utcnow()
        try:
            response = fetch_text(url, accept="application/rss+xml,application/xml,*/*;q=0.5")
            rows = parse_feed(response.text, response.url, request.limit)
        except Exception as exc:
            return CollectionResult(
                self.id,
                ProviderState.FAILED,
                warnings=[f"Google News RSS failed: {type(exc).__name__}: {exc}"],
            )

        events = [
            RawEvent(
                id=stable_id(self.id, row.get("url"), row.get("title")),
                provider=self.id,
                source=self.source,
                acquisition_method=AcquisitionMethod.RSS,
                external_id=str(row.get("external_id") or row.get("url") or ""),
                url=row.get("url"),
                title=row.get("title"),
                text=row.get("text"),
                published_at=row.get("published_at"),
                retrieved_at=now,
                language=request.language,
                country=request.country,
                metrics={"mention": 1.0},
                raw={"query": query},
                provenance=Provenance(
                    terms_class=self.terms_class,
                    endpoint=url,
                ),
            )
            for row in rows
        ]
        return CollectionResult(
            self.id,
            ProviderState.HEALTHY if events else ProviderState.DEGRADED,
            events=events,
            warnings=[] if events else ["Google News returned no usable RSS items"],
        )


class GDELTProvider(DataProvider):
    id = "gdelt"
    source = "gdeltproject.org"
    primary = "GDELT DOC 2.0 API"
    fallback = "none; provider may degrade independently"
    terms_class = "public_api"

    def collect(self, request: CollectionRequest) -> CollectionResult:
        params = urllib.parse.urlencode({
            "query": request.topic,
            "mode": "artlist",
            "format": "json",
            "maxrecords": min(max(request.limit, 1), 75),
            "sort": "datedesc",
            "timespan": "3d",
        })
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + params
        try:
            data, _ = _json_request(url, timeout=25.0)
        except Exception as exc:
            return CollectionResult(
                self.id,
                ProviderState.DEGRADED,
                warnings=[f"GDELT unavailable: {type(exc).__name__}: {exc}"],
            )

        now = utcnow()
        events: list[RawEvent] = []
        for item in (data.get("articles") or [])[: request.limit]:
            article_url = item.get("url")
            title = item.get("title") or article_url
            if not title:
                continue
            seen = item.get("seendate")
            published = None
            if seen:
                try:
                    published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except Exception:
                    published = None
            events.append(
                RawEvent(
                    id=stable_id(self.id, article_url, title),
                    provider=self.id,
                    source=self.source,
                    acquisition_method=AcquisitionMethod.PUBLIC_WEB_API,
                    external_id=str(article_url or title),
                    url=article_url,
                    title=title,
                    text=title,
                    author=item.get("domain"),
                    community=item.get("domain"),
                    published_at=published,
                    retrieved_at=now,
                    language=item.get("language"),
                    country=item.get("sourcecountry"),
                    metrics={"mention": 1.0},
                    raw={"query": request.topic},
                    provenance=Provenance(
                        terms_class=self.terms_class,
                        endpoint="https://api.gdeltproject.org/api/v2/doc/doc",
                    ),
                )
            )
        return CollectionResult(
            self.id,
            ProviderState.HEALTHY if events else ProviderState.DEGRADED,
            events=events,
            warnings=[] if events else ["GDELT returned no usable articles"],
        )


def _title_key(event: RawEvent) -> str:
    title = normalize_text(event.title or "")
    title = re.sub(r"\b(the|a|an|and|for|to|of|in|on)\b", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def dedupe_news(events: list[RawEvent]) -> list[RawEvent]:
    """Cross-provider near-duplicate removal for Google News/GDELT."""
    kept: list[RawEvent] = []
    news_keys: list[str] = []
    for event in events:
        if event.provider not in {"google_news", "gdelt"}:
            kept.append(event)
            continue
        key = _title_key(event)
        duplicate = any(
            key and existing and difflib.SequenceMatcher(None, key, existing).ratio() >= 0.88
            for existing in news_keys
        )
        if duplicate:
            continue
        kept.append(event)
        news_keys.append(key)
    return kept


def _freshness_seconds(event: RawEvent, now: datetime) -> int:
    source_time = event.published_at or event.retrieved_at
    try:
        return max(0, int((now - source_time).total_seconds()))
    except Exception:
        return 0


def event_to_signal(event: RawEvent, topic_id: str, resolver: TopicResolver) -> Signal:
    now = utcnow()
    kind = (event.raw or {}).get("kind") if isinstance(event.raw, dict) else None
    signal_type = "momentum"
    confidence = 75.0
    raw_value = 1.0

    if event.provider == "github" and kind == "repository":
        stars = event.metrics.get("stars", 0.0)
        forks = event.metrics.get("forks", 0.0)
        raw_value = stars + 2.0 * forks
        normalized = clamp(math.log10(1.0 + raw_value) / 5.0 * 100.0)
        signal_type = "momentum"
        confidence = 88.0
    elif event.provider == "github" and kind == "issue":
        raw_value = 1.0 + event.metrics.get("comments", 0.0) + event.metrics.get("reactions", 0.0)
        normalized = clamp(25.0 + math.log10(1.0 + raw_value) / 2.5 * 75.0)
        signal_type = "demand"
        confidence = 82.0
    elif event.provider == "hackernews":
        raw_value = 1.0 + event.metrics.get("points", 0.0) + 2.0 * event.metrics.get("comments", 0.0)
        normalized = clamp(20.0 + math.log10(1.0 + raw_value) / 3.0 * 80.0)
        signal_type = "demand"
        confidence = 80.0
    elif event.provider in {"google_news", "gdelt"}:
        raw_value = 1.0
        normalized = 55.0
        signal_type = "media"
        confidence = 72.0 if event.provider == "google_news" else 68.0
    elif event.provider == "website":
        raw_value = 1.0
        normalized = 60.0
        signal_type = "supply"
        confidence = 85.0
    else:
        normalized = 50.0

    return Signal(
        id=stable_id("signal", event.id, topic_id),
        topic_id=topic_id,
        entity_ids=resolver.event_entities(event),
        signal_type=signal_type,
        source=event.source,
        provider=event.provider,
        observed_at=event.retrieved_at,
        value=float(raw_value),
        normalized_value=float(normalized),
        confidence=confidence,
        evidence_ids=[event.id],
        freshness_seconds=_freshness_seconds(event, now),
        geo=event.country,
        language=event.language,
    )


def snapshot_events(
    *,
    store: SnapshotStore,
    topic_id: str,
    result: CollectionResult,
) -> None:
    now = result.collected_at
    store.append(MetricSnapshot("topic", topic_id, "event_count", len(result.events), now, result.provider))

    engagement_sum = 0.0
    for event in result.events:
        for metric, value in event.metrics.items():
            engagement_sum += float(value)
            if event.provider == "github" and metric in {"stars", "forks", "open_issues"}:
                store.append(
                    MetricSnapshot(
                        "repo",
                        event.external_id or event.id,
                        metric,
                        float(value),
                        now,
                        event.provider,
                    )
                )
    store.append(MetricSnapshot("topic", topic_id, "engagement_sum", engagement_sum, now, result.provider))


def historical_momentum_signals(
    *,
    store: SnapshotStore,
    topic_id: str,
    provider_ids: list[str],
) -> list[Signal]:
    rows: list[Signal] = []
    now = utcnow()
    for provider in provider_ids:
        series = store.series("topic", topic_id, "event_count", provider)
        if len(series) < 2:
            continue
        elapsed = (series[-1].collected_at - series[-2].collected_at).total_seconds()
        # Re-running the demo seconds apart must not manufacture enormous
        # per-day velocity. Production collectors should run on a real cadence.
        if elapsed < 300:
            continue
        vel = velocity(series)
        acc = acceleration(series)
        # No positive movement => no historical trend evidence.
        strength = max(0.0, vel) + 0.5 * max(0.0, acc)
        normalized = clamp(100.0 * (1.0 - math.exp(-strength / 5.0))) if strength > 0 else 0.0
        rows.append(
            Signal(
                id=stable_id("history", topic_id, provider, series[-1].collected_at.isoformat()),
                topic_id=topic_id,
                entity_ids=[],
                signal_type="momentum",
                source=provider,
                provider=provider,
                observed_at=series[-1].collected_at,
                value=vel,
                normalized_value=normalized,
                confidence=90.0 if len(series) >= 3 else 75.0,
                evidence_ids=[],
                freshness_seconds=max(0, int((now - series[-1].collected_at).total_seconds())),
                baseline=series[-2].value,
                delta=series[-1].value - series[-2].value,
                velocity=vel,
                acceleration=acc,
            )
        )
    return rows


def run_pipeline(
    *,
    topic: str,
    limit: int = 10,
    snapshot_path: str = ".radar/snapshots.jsonl",
    cache_path: str = ".radar/provider-cache.json",
    website: str | None = None,
) -> dict[str, Any]:
    request = CollectionRequest(topic=topic, limit=limit)
    resolver = default_resolver()
    topic_node = resolver.resolve(topic)

    registry = ProviderRegistry()
    cache = EventCache(cache_path)
    for provider in (GitHubProvider(), HackerNewsProvider(), GoogleNewsProvider(), GDELTProvider()):
        registry.register(provider)
    if website:
        registry.register(WebsiteProvider())

    def collect(provider: DataProvider) -> CollectionResult:
        provider_request = request
        if provider.id == "website":
            provider_request = CollectionRequest(
                topic=topic,
                limit=limit,
                metadata={"url": website, "purpose": "competitor"},
            )

        result = registry.safe_collect(provider, provider_request)
        if result.events and result.status in {ProviderState.HEALTHY, ProviderState.DEGRADED}:
            cache.save(provider.id, topic, result.events)
            return result

        if not result.events and result.status in {
            ProviderState.FAILED,
            ProviderState.RATE_LIMITED,
            ProviderState.DEGRADED,
        }:
            stale_events = cache.load(provider.id, topic)
            if stale_events:
                return CollectionResult(
                    provider=provider.id,
                    status=ProviderState.STALE,
                    events=stale_events,
                    warnings=[*result.warnings, "using last successful cached events"],
                    rate_limit=result.rate_limit,
                )

        return result

    results: list[CollectionResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(registry.providers()))) as pool:
        futures = {pool.submit(collect, provider): provider.id for provider in registry.providers()}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    # Deterministic provider order makes CLI/test output stable.
    results.sort(key=lambda result: result.provider)

    all_events = dedupe_news([event for result in results for event in result.events])
    allowed_ids = {event.id for event in all_events}
    for result in results:
        result.events = [event for event in result.events if event.id in allowed_ids]

    store = SnapshotStore(snapshot_path)
    for result in results:
        snapshot_events(store=store, topic_id=topic_node.id, result=result)

    signals = [
        event_to_signal(event, topic_node.id, resolver)
        for event in all_events
    ]
    signals.extend(
        historical_momentum_signals(
            store=store,
            topic_id=topic_node.id,
            provider_ids=[result.provider for result in results],
        )
    )

    opportunity = score_opportunity(
        topic_id=topic_node.id,
        topic=topic_node.name,
        signals=signals,
    )

    provider_summary = {
        result.provider: {
            "status": result.status.value,
            "event_count": len(result.events),
            "warnings": result.warnings,
            "rate_limit": {
                "remaining": result.rate_limit.remaining,
                "reset_at": result.rate_limit.reset_at.isoformat() if result.rate_limit and result.rate_limit.reset_at else None,
            } if result.rate_limit else None,
        }
        for result in results
    }

    return {
        "topic": topic_node.name,
        "topic_id": topic_node.id,
        "providers": provider_summary,
        "event_count": len(all_events),
        "evidence_event_count": len({eid for signal in signals for eid in signal.evidence_ids}),
        "snapshot_count": len(store.all()),
        "features": summarize_signals(signals).to_dict(),
        "opportunity": opportunity.to_dict(),
        "events": [event.to_dict() for event in all_events],
        "signals": [signal.to_dict() for signal in signals],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Content Opportunity Radar Phase-1 demo")
    parser.add_argument("--topic", default="AI Agents")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--snapshot-path", default=".radar/snapshots.jsonl")
    parser.add_argument("--website", help="Optional competitor/user website to add supply signals")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = run_pipeline(
        topic=args.topic,
        limit=max(1, min(args.limit, 30)),
        snapshot_path=args.snapshot_path,
        website=args.website,
    )
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
