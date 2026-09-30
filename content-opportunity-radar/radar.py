"""Opportunity Discovery Loop V1.

Example:
    python radar.py --discover --scope "AI" --top 5

The discovery layer only proposes topics observed in real source data. The
existing pipeline performs the deeper, evidence-backed opportunity scoring.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

from collector import RadarStore
from core import (
    CollectionRequest,
    CollectionResult,
    DataProvider,
    MetricSnapshot,
    ProviderRegistry,
    ProviderState,
    SnapshotStore,
    utcnow,
)
from discovery import CandidateTopic, discover_candidates, overlap_penalized_rank
from gsc import GSCProvider
from pipeline import (
    GDELTProvider,
    GitHubProvider,
    GoogleNewsProvider,
    HackerNewsProvider,
    dedupe_news,
    run_pipeline,
)
from read_model import OpportunityReadStore
from research_pack import build_research_pack
from web import WebsiteProvider


@dataclass(slots=True)
class DiscoverySeed:
    events: list
    providers: dict[str, dict[str, Any]]
    source_mode: str = "live"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": len(self.events),
            "providers": self.providers,
            "source_mode": self.source_mode,
            "warnings": self.warnings,
        }


def collect_seed_events(
    *,
    scope: str,
    limit: int = 20,
    website: str | None = None,
    gsc_site_url: str | None = None,
    gsc_access_token: str | None = None,
    providers: Sequence[DataProvider] | None = None,
    collector_db: str | None = None,
    collector_since_hours: int = 72,
    collector_job_prefix: str | None = None,
    language: str = "en",
    country: str = "US",
) -> DiscoverySeed:
    """Collect broad source evidence used only to propose candidate topics.

    If a persistent collector database has recent events, seed discovery reads
    those deduped events instead of re-hitting broad source APIs. Deep scans of
    shortlisted candidates remain live/evidence-specific.
    """
    seed_warnings: list[str] = []
    if collector_db:
        since = utcnow() - timedelta(
            hours=max(1, min(int(collector_since_hours), 24 * 365))
        )
        seed_providers = [
            "github",
            "hackernews",
            "google_news",
            "gdelt",
            "gsc",
            "website",
        ]
        try:
            with RadarStore(collector_db) as store:
                stored_events = store.recent_events(
                    since=since,
                    providers=seed_providers,
                    job_prefix=collector_job_prefix,
                    limit=max(500, min(50000, limit * 200)),
                )
        except Exception as exc:
            stored_events = []
            seed_warnings.append(
                f"collector store unavailable; live seed fallback: {type(exc).__name__}: {exc}"
            )

        if stored_events:
            stored_events = dedupe_news(stored_events)
            counts: dict[str, int] = {}
            for event in stored_events:
                counts[event.provider] = counts.get(event.provider, 0) + 1
            return DiscoverySeed(
                events=stored_events,
                providers={
                    provider: {
                        "status": "stored",
                        "event_count": count,
                        "warnings": [],
                    }
                    for provider, count in sorted(counts.items())
                },
                source_mode="collector_store",
                warnings=seed_warnings,
            )

        seed_warnings.append(
            "collector store had no recent seed events; live seed collection used"
        )

    registry = ProviderRegistry()
    base = list(providers) if providers is not None else [
        GitHubProvider(),
        HackerNewsProvider(),
        GoogleNewsProvider(),
        GDELTProvider(),
    ]
    for provider in base:
        registry.register(provider)

    registered = {provider.id for provider in registry.providers()}
    if website and "website" not in registered:
        registry.register(WebsiteProvider())
        registered.add("website")
    if gsc_site_url and "gsc" not in registered:
        registry.register(GSCProvider())

    def collect(provider: DataProvider) -> CollectionResult:
        if provider.id == "website":
            request = CollectionRequest(
                topic=scope,
                limit=limit,
                language=language,
                country=country,
                metadata={"url": website, "purpose": "discovery"},
            )
        elif provider.id == "gsc":
            request = CollectionRequest(
                topic=scope,
                limit=limit,
                language=language,
                country=country,
                metadata={
                    "site_url": gsc_site_url,
                    "access_token": gsc_access_token,
                    "query_filter": "",
                    "dimensions": ["date", "query", "page"],
                    "row_limit": 5000,
                    "max_rows": 5000,
                },
            )
        else:
            request = CollectionRequest(
                topic=scope,
                limit=limit,
                language=language,
                country=country,
            )
        return registry.safe_collect(provider, request)

    results: list[CollectionResult] = []
    max_workers = min(5, max(1, len(registry.providers())))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(collect, provider) for provider in registry.providers()]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda row: row.provider)
    events = dedupe_news([event for result in results for event in result.events])

    summary = {
        result.provider: {
            "status": result.status.value,
            "event_count": len(result.events),
            "warnings": result.warnings,
        }
        for result in results
    }
    return DiscoverySeed(
        events=events,
        providers=summary,
        source_mode="live",
        warnings=seed_warnings,
    )


def _candidate_scan(
    candidate: CandidateTopic,
    *,
    limit: int,
    snapshot_path: str,
    cache_path: str,
    website: str | None,
    gsc_site_url: str | None,
    gsc_access_token: str | None,
    google_ads_customer_id: str | None,
    google_ads_access_token: str | None,
    google_ads_login_customer_id: str | None,
    google_ads_geo_target: str,
    google_ads_language: str,
    hydrate_content: bool,
    content_max_pages: int,
    content_snapshot_path: str,
    language: str,
    country: str,
) -> dict[str, Any]:
    report = run_pipeline(
        topic=candidate.name,
        limit=limit,
        language=language,
        country=country,
        snapshot_path=snapshot_path,
        cache_path=cache_path,
        website=website,
        gsc_site_url=gsc_site_url,
        gsc_access_token=gsc_access_token,
        gsc_query_filter=candidate.name,
        google_ads_customer_id=google_ads_customer_id,
        google_ads_access_token=google_ads_access_token,
        google_ads_login_customer_id=google_ads_login_customer_id,
        google_ads_geo_target=google_ads_geo_target,
        google_ads_language=google_ads_language,
        hydrate_content=hydrate_content,
        content_max_pages=content_max_pages,
        content_snapshot_path=content_snapshot_path,
    )
    report["discovery"] = candidate.to_dict()
    return report


def persist_radar_rankings(
    reports: Sequence[dict[str, Any]],
    *,
    snapshot_path: str,
) -> None:
    store = SnapshotStore(snapshot_path)
    now = utcnow()

    for rank, report in enumerate(reports, start=1):
        topic_id = str(report.get("topic_id") or "")
        if not topic_id:
            continue

        opportunity = report.get("opportunity") or {}
        discovery = report.get("discovery") or {}
        values = {
            "radar_opportunity_score": float(opportunity.get("score") or 0.0),
            "radar_rank_score": float(report.get("rank_score") or 0.0),
            "radar_discovery_score": float(discovery.get("discovery_score") or 0.0),
            "radar_rank": float(rank),
        }
        for metric, value in values.items():
            store.append(
                MetricSnapshot(
                    "topic",
                    topic_id,
                    metric,
                    value,
                    now,
                    "radar",
                )
            )


def _workspace_path(path: str, workspace_id: str | None) -> str:
    """Keep append-only radar artifacts inside one workspace namespace."""
    if not workspace_id:
        return path
    source = Path(path)
    return str(source.parent / workspace_id / source.name)


def run_discovery(
    *,
    scope: str = "AI",
    seed_limit: int = 20,
    candidate_count: int = 8,
    top_n: int = 5,
    deep_limit: int = 10,
    snapshot_path: str = ".radar/snapshots.jsonl",
    cache_path: str = ".radar/provider-cache.json",
    website: str | None = None,
    gsc_site_url: str | None = None,
    gsc_access_token: str | None = None,
    google_ads_customer_id: str | None = None,
    google_ads_access_token: str | None = None,
    google_ads_login_customer_id: str | None = None,
    google_ads_geo_target: str = "2840",
    google_ads_language: str = "1000",
    hydrate_content: bool = False,
    content_max_pages: int = 20,
    content_snapshot_path: str = ".radar/page-snapshots.jsonl",
    seed_providers: Sequence[DataProvider] | None = None,
    collector_db: str | None = None,
    collector_since_hours: int = 72,
    collector_job_prefix: str | None = None,
    read_model_db: str | None = None,
    workspace_id: str | None = None,
    language: str = "en",
    country: str = "US",
    include_research_pack: bool = False,
) -> dict[str, Any]:
    snapshot_path = _workspace_path(snapshot_path, workspace_id)
    cache_path = _workspace_path(cache_path, workspace_id)
    content_snapshot_path = _workspace_path(content_snapshot_path, workspace_id)

    seed = collect_seed_events(
        scope=scope,
        limit=max(1, min(seed_limit, 50)),
        website=website,
        gsc_site_url=gsc_site_url,
        gsc_access_token=gsc_access_token,
        providers=seed_providers,
        collector_db=collector_db,
        collector_since_hours=collector_since_hours,
        collector_job_prefix=collector_job_prefix,
        language=language,
        country=country,
    )
    candidates = discover_candidates(
        seed.events,
        scope=scope,
        max_candidates=max(1, min(candidate_count, 30)),
    )

    reports: list[dict[str, Any]] = []
    scan_errors: list[dict[str, str]] = []

    # Sequential deep scans are deliberate: search APIs have tighter rate
    # limits than normal REST traffic and a Radar should prefer stability over
    # bursting dozens of queries at once.
    for candidate in candidates:
        try:
            reports.append(
                _candidate_scan(
                    candidate,
                    limit=max(1, min(deep_limit, 30)),
                    snapshot_path=snapshot_path,
                    cache_path=cache_path,
                    website=website,
                    gsc_site_url=gsc_site_url,
                    gsc_access_token=gsc_access_token,
                    google_ads_customer_id=google_ads_customer_id,
                    google_ads_access_token=google_ads_access_token,
                    google_ads_login_customer_id=google_ads_login_customer_id,
                    google_ads_geo_target=google_ads_geo_target,
                    google_ads_language=google_ads_language,
                    hydrate_content=hydrate_content,
                    content_max_pages=max(1, min(content_max_pages, 100)),
                    content_snapshot_path=content_snapshot_path,
                    language=language,
                    country=country,
                )
            )
        except Exception as exc:
            scan_errors.append({
                "topic": candidate.name,
                "error": f"{type(exc).__name__}: {exc}",
            })

    ranked = overlap_penalized_rank(reports)
    top = ranked[: max(1, min(top_n, len(ranked) or 1))]
    if include_research_pack:
        top = [
            {
                **row,
                "research_pack": build_research_pack(row),
            }
            for row in top
        ]
    persist_radar_rankings(top, snapshot_path=snapshot_path)

    output = {
        "mode": "discovery",
        "workspace_id": workspace_id,
        "scope": scope,
        "seed": seed.to_dict(),
        "candidate_count": len(candidates),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "scanned_count": len(reports),
        "scan_errors": scan_errors,
        "top_opportunities": top,
    }
    if read_model_db:
        with OpportunityReadStore(read_model_db) as store:
            run_id = store.record_discovery(output)
        output["read_model"] = {
            "database": read_model_db,
            "run_id": run_id,
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Content Opportunity Radar discovery loop")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Run automatic candidate discovery (default behavior of this command)",
    )
    parser.add_argument("--scope", default="AI", help="Broad domain used for seed collection")
    parser.add_argument("--workspace-id")
    parser.add_argument("--language", default="en")
    parser.add_argument("--country", default="US")
    parser.add_argument("--seed-limit", type=int, default=20)
    parser.add_argument("--candidates", type=int, default=8)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--deep-limit", type=int, default=10)
    parser.add_argument("--snapshot-path", default=".radar/snapshots.jsonl")
    parser.add_argument("--cache-path", default=".radar/provider-cache.json")
    parser.add_argument("--website")
    parser.add_argument("--gsc-site")
    parser.add_argument("--google-ads-customer")
    parser.add_argument("--google-ads-login-customer")
    parser.add_argument("--google-ads-geo-target", default="2840")
    parser.add_argument("--google-ads-language", default="1000")
    parser.add_argument("--hydrate-content", action="store_true")
    parser.add_argument("--content-max-pages", type=int, default=20)
    parser.add_argument("--content-snapshot-path", default=".radar/page-snapshots.jsonl")
    parser.add_argument(
        "--collector-db",
        help="Use recent persistent collector events for broad seed discovery",
    )
    parser.add_argument(
        "--collector-since-hours",
        type=int,
        default=72,
        help="Maximum age of stored seed events",
    )
    parser.add_argument(
        "--read-model-db",
        help="Persist Radar runs/opportunities for the read-only API",
    )
    parser.add_argument(
        "--research-pack",
        action="store_true",
        help="Attach a provenance-first Research Pack to each Top Opportunity",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = run_discovery(
        scope=args.scope,
        seed_limit=args.seed_limit,
        candidate_count=args.candidates,
        top_n=args.top,
        deep_limit=args.deep_limit,
        snapshot_path=args.snapshot_path,
        cache_path=args.cache_path,
        website=args.website,
        gsc_site_url=args.gsc_site,
        gsc_access_token=os.getenv("GSC_ACCESS_TOKEN"),
        google_ads_customer_id=args.google_ads_customer,
        google_ads_access_token=os.getenv("GOOGLE_ADS_ACCESS_TOKEN"),
        google_ads_login_customer_id=args.google_ads_login_customer,
        google_ads_geo_target=args.google_ads_geo_target,
        google_ads_language=args.google_ads_language,
        hydrate_content=args.hydrate_content,
        content_max_pages=args.content_max_pages,
        content_snapshot_path=args.content_snapshot_path,
        collector_db=args.collector_db,
        collector_since_hours=args.collector_since_hours,
        read_model_db=args.read_model_db,
        workspace_id=args.workspace_id,
        language=args.language,
        country=args.country,
        include_research_pack=args.research_pack,
    )
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
