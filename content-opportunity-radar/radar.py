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
from dataclasses import dataclass
from typing import Any, Sequence

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
from web import WebsiteProvider


@dataclass(slots=True)
class DiscoverySeed:
    events: list
    providers: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": len(self.events),
            "providers": self.providers,
        }


def collect_seed_events(
    *,
    scope: str,
    limit: int = 20,
    website: str | None = None,
    gsc_site_url: str | None = None,
    gsc_access_token: str | None = None,
    providers: Sequence[DataProvider] | None = None,
) -> DiscoverySeed:
    """Collect broad source evidence used only to propose candidate topics."""
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
                metadata={"url": website, "purpose": "discovery"},
            )
        elif provider.id == "gsc":
            request = CollectionRequest(
                topic=scope,
                limit=limit,
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
            request = CollectionRequest(topic=scope, limit=limit)
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
    return DiscoverySeed(events=events, providers=summary)


def _candidate_scan(
    candidate: CandidateTopic,
    *,
    limit: int,
    snapshot_path: str,
    cache_path: str,
    website: str | None,
    gsc_site_url: str | None,
    gsc_access_token: str | None,
    hydrate_content: bool,
    content_max_pages: int,
    content_snapshot_path: str,
) -> dict[str, Any]:
    report = run_pipeline(
        topic=candidate.name,
        limit=limit,
        snapshot_path=snapshot_path,
        cache_path=cache_path,
        website=website,
        gsc_site_url=gsc_site_url,
        gsc_access_token=gsc_access_token,
        gsc_query_filter=candidate.name,
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
    hydrate_content: bool = False,
    content_max_pages: int = 20,
    content_snapshot_path: str = ".radar/page-snapshots.jsonl",
    seed_providers: Sequence[DataProvider] | None = None,
) -> dict[str, Any]:
    seed = collect_seed_events(
        scope=scope,
        limit=max(1, min(seed_limit, 50)),
        website=website,
        gsc_site_url=gsc_site_url,
        gsc_access_token=gsc_access_token,
        providers=seed_providers,
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
                    hydrate_content=hydrate_content,
                    content_max_pages=max(1, min(content_max_pages, 100)),
                    content_snapshot_path=content_snapshot_path,
                )
            )
        except Exception as exc:
            scan_errors.append({
                "topic": candidate.name,
                "error": f"{type(exc).__name__}: {exc}",
            })

    ranked = overlap_penalized_rank(reports)
    top = ranked[: max(1, min(top_n, len(ranked) or 1))]
    persist_radar_rankings(top, snapshot_path=snapshot_path)

    return {
        "mode": "discovery",
        "scope": scope,
        "seed": seed.to_dict(),
        "candidate_count": len(candidates),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "scanned_count": len(reports),
        "scan_errors": scan_errors,
        "top_opportunities": top,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Content Opportunity Radar discovery loop")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Run automatic candidate discovery (default behavior of this command)",
    )
    parser.add_argument("--scope", default="AI", help="Broad domain used for seed collection")
    parser.add_argument("--seed-limit", type=int, default=20)
    parser.add_argument("--candidates", type=int, default=8)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--deep-limit", type=int, default=10)
    parser.add_argument("--snapshot-path", default=".radar/snapshots.jsonl")
    parser.add_argument("--cache-path", default=".radar/provider-cache.json")
    parser.add_argument("--website")
    parser.add_argument("--gsc-site")
    parser.add_argument("--hydrate-content", action="store_true")
    parser.add_argument("--content-max-pages", type=int, default=20)
    parser.add_argument("--content-snapshot-path", default=".radar/page-snapshots.jsonl")
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
        hydrate_content=args.hydrate_content,
        content_max_pages=args.content_max_pages,
        content_snapshot_path=args.content_snapshot_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
