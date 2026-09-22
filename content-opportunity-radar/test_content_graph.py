from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from content_graph import (
    PageDocument,
    PageHydrator,
    PageSnapshotStore,
    RobotsPolicy,
    SiteCrawler,
    analyze_gsc_site_content,
    page_query_relevance,
)
from core import AcquisitionMethod, CollectionRequest, CollectionResult, DataProvider, ProviderState, Provenance, RawEvent, stable_id
from opportunity import score_opportunity
from pipeline import run_pipeline
from web import FetchResponse


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


class FakeFetcher:
    def __init__(self, pages: dict[str, tuple[str, str]]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append(url)
        if url not in self.pages:
            raise FileNotFoundError(url)
        content_type, text = self.pages[url]
        return FetchResponse(
            url=url,
            status=200,
            content_type=content_type,
            text=text,
        )


def page_doc(
    url: str,
    *,
    title: str,
    h1: list[str],
    h2: list[str] | None = None,
    text: str = "",
    modified_at: datetime | None = None,
) -> PageDocument:
    canonical = url.rstrip("/")
    content_hash = stable_id(title, h1, h2 or [], text)
    return PageDocument(
        id=f"page:{stable_id(canonical)}",
        url=canonical,
        canonical_url=canonical,
        domain="example.com",
        title=title,
        description="",
        h1=h1,
        h2=h2 or [],
        main_text=text,
        internal_links=[],
        content_hash=content_hash,
        retrieved_at=NOW,
        published_at=None,
        modified_at=modified_at,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


def gsc_event(
    day: date,
    *,
    query: str,
    page: str,
    impressions: float,
    clicks: float,
    position: float,
) -> RawEvent:
    event_id = stable_id("gsc-content-test", day.isoformat(), query, page)
    return RawEvent(
        id=event_id,
        provider="gsc",
        source="sc-domain:example.com",
        acquisition_method=AcquisitionMethod.OAUTH_API,
        retrieved_at=NOW,
        external_id=event_id,
        url=page,
        title=query,
        text=query,
        published_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        metrics={
            "clicks": clicks,
            "impressions": impressions,
            "ctr": clicks / impressions if impressions else 0.0,
            "position": position,
        },
        raw={
            "dimensions": {
                "date": day.isoformat(),
                "query": query,
                "page": page,
            },
            "completeness": "top_rows_only_not_exhaustive",
        },
        provenance=Provenance("first_party_oauth_top_rows", "v3", "test"),
    )


def gsc_history(
    *,
    query: str,
    pages: list[str],
    recent_impressions: float = 30,
    baseline_impressions: float = 10,
    recent_ctr: float = 0.05,
    baseline_ctr: float = 0.05,
    recent_position: float = 15,
    baseline_position: float = 20,
) -> list[RawEvent]:
    latest = date(2026, 9, 21)
    start = latest - timedelta(days=34)
    rows: list[RawEvent] = []
    for offset in range(35):
        day = start + timedelta(days=offset)
        recent = day >= latest - timedelta(days=6)
        impressions = recent_impressions if recent else baseline_impressions
        ctr = recent_ctr if recent else baseline_ctr
        position = recent_position if recent else baseline_position
        for page in pages:
            per_page_impressions = impressions / len(pages)
            rows.append(
                gsc_event(
                    day,
                    query=query,
                    page=page,
                    impressions=per_page_impressions,
                    clicks=per_page_impressions * ctr,
                    position=position,
                )
            )
    return rows


class StaticGSCProvider(DataProvider):
    id = "gsc"

    def __init__(self, events: list[RawEvent]):
        self.events = events

    def collect(self, request: CollectionRequest):
        return CollectionResult(
            provider=self.id,
            status=ProviderState.HEALTHY,
            events=list(self.events),
        )


class ContentGraphTests(unittest.TestCase):
    def test_hydrator_extracts_semantic_content_and_internal_links(self):
        url = "https://example.com/agents"
        fetcher = FakeFetcher({
            url: (
                "text/html; charset=utf-8",
                """
                <html>
                  <head>
                    <title>AI Agent Memory Guide</title>
                    <meta name="description" content="A practical guide">
                    <meta property="article:published_time" content="2026-01-02T10:00:00Z">
                    <meta property="article:modified_time" content="2026-09-01T11:00:00Z">
                    <link rel="canonical" href="/agents">
                  </head>
                  <body>
                    <nav>navigation noise</nav>
                    <main>
                      <h1>AI Agent Memory</h1>
                      <h2>Long-term memory architecture</h2>
                      <p>This article explains agent memory architecture in depth.</p>
                      <a href="/related">Related</a>
                      <a href="https://outside.example.net/x">External</a>
                    </main>
                  </body>
                </html>
                """,
            )
        })
        doc = PageHydrator(fetcher).hydrate(url)

        self.assertEqual(doc.canonical_url, url)
        self.assertEqual(doc.title, "AI Agent Memory Guide")
        self.assertEqual(doc.h1, ["AI Agent Memory"])
        self.assertIn("Long-term memory architecture", doc.h2)
        self.assertIn("agent memory architecture", doc.main_text)
        self.assertEqual(doc.internal_links, ["https://example.com/related"])
        self.assertEqual(doc.modified_at.year, 2026)
        self.assertTrue(doc.content_hash)

    def test_crawler_respects_robots_and_page_budget(self):
        fetcher = FakeFetcher({
            "https://example.com/robots.txt": (
                "text/plain",
                "User-agent: *\nDisallow: /private\n",
            ),
            "https://example.com/a": ("text/html", "<html><title>A</title><p>A page</p></html>"),
            "https://example.com/b": ("text/html", "<html><title>B</title><p>B page</p></html>"),
            "https://example.com/private": ("text/html", "<html><title>Private</title></html>"),
        })
        crawler = SiteCrawler(
            hydrator=PageHydrator(fetcher),
            robots=RobotsPolicy(fetcher),
            sleep_fn=lambda seconds: None,
        )
        report = crawler.hydrate_urls(
            [
                "https://example.com/private",
                "https://example.com/a",
                "https://example.com/b",
            ],
            max_pages=1,
        )
        self.assertEqual(len(report.pages), 1)
        self.assertEqual(report.pages[0].url, "https://example.com/a")
        self.assertIn("https://example.com/private", report.skipped)
        self.assertTrue(any("robots.txt disallows" in warning for warning in report.warnings))

    def test_snapshot_store_detects_content_hash_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PageSnapshotStore(Path(tmp) / "pages.jsonl")
            first = page_doc(
                "https://example.com/agents",
                title="Agent Guide",
                h1=["AI Agents"],
                text="version one",
            )
            first.retrieved_at = NOW - timedelta(days=1)
            store.append(first)

            second = page_doc(
                "https://example.com/agents",
                title="Agent Guide",
                h1=["AI Agents"],
                text="version two",
            )
            stored = store.append(second)

            self.assertTrue(stored.changed)
            self.assertEqual(stored.previous_content_hash, first.content_hash)
            self.assertEqual(stored.first_seen_at, first.retrieved_at)

            reloaded = PageSnapshotStore(Path(tmp) / "pages.jsonl")
            self.assertEqual(len(reloaded.history("https://example.com/agents")), 2)

    def test_dedicated_page_reduces_query_page_gap_and_supply_gap(self):
        query = "ai agent memory"
        url = "https://example.com/agent-memory"
        events = gsc_history(query=query, pages=[url])
        page = page_doc(
            url,
            title="AI Agent Memory: Architecture Guide",
            h1=["AI Agent Memory Architecture"],
            h2=["Long-term memory"],
            text="A complete guide to AI agent memory patterns.",
        )

        analysis = analyze_gsc_site_content(
            events,
            [page],
            topic_id="topic:ai-agents",
            query_terms=["ai agent"],
        )
        row = analysis.query_opportunities[0]
        self.assertGreaterEqual(row.best_relevance, 55)
        self.assertLess(row.query_page_gap_score, row.demand_score / 2)
        self.assertTrue(row.dedicated_page_ids)
        self.assertIsNotNone(analysis.supply_signal)
        self.assertGreaterEqual(analysis.supply_signal.normalized_value, 55)

        opportunity = score_opportunity(
            topic_id="topic:ai-agents",
            topic="AI Agents",
            signals=[analysis.supply_signal],
        )
        self.assertLess(opportunity.components["supply_gap"], 45)

    def test_unrelated_hydrated_page_creates_supply_gap(self):
        query = "ai agent memory"
        url = "https://example.com/general"
        events = gsc_history(query=query, pages=[url])
        page = page_doc(
            url,
            title="Company News",
            h1=["Quarterly Update"],
            text="Revenue, hiring and office updates.",
        )

        analysis = analyze_gsc_site_content(
            events,
            [page],
            topic_id="topic:ai-agents",
            query_terms=["ai agent"],
        )
        row = analysis.query_opportunities[0]
        self.assertLess(row.best_relevance, 30)
        self.assertGreater(row.query_page_gap_score, 0)
        self.assertLess(analysis.supply_signal.normalized_value, 30)

        opportunity = score_opportunity(
            topic_id="topic:ai-agents",
            topic="AI Agents",
            signals=[analysis.supply_signal],
        )
        self.assertGreater(opportunity.components["supply_gap"], 70)

    def test_missing_hydration_is_unknown_not_false_gap(self):
        events = gsc_history(
            query="ai agent memory",
            pages=["https://example.com/not-hydrated"],
        )
        analysis = analyze_gsc_site_content(
            events,
            [],
            topic_id="topic:ai-agents",
            query_terms=["ai agent"],
        )
        row = analysis.query_opportunities[0]
        self.assertEqual(row.query_page_gap_score, 0.0)
        self.assertEqual(row.confidence, 0.0)
        self.assertIsNone(analysis.supply_signal)

    def test_cannibalization_detects_material_second_page_share(self):
        query = "ai agent platform"
        pages = [
            "https://example.com/ai-agent-platform",
            "https://example.com/agent-platform-guide",
        ]
        events = gsc_history(query=query, pages=pages)
        docs = [
            page_doc(
                pages[0],
                title="AI Agent Platform",
                h1=["AI Agent Platform"],
                text="AI agent platform guide.",
            ),
            page_doc(
                pages[1],
                title="AI Agent Platform Guide",
                h1=["AI Agent Platform Guide"],
                text="How to choose an AI agent platform.",
            ),
        ]
        analysis = analyze_gsc_site_content(
            events,
            docs,
            topic_id="topic:ai-agents",
            query_terms=["ai agent"],
        )
        self.assertGreater(analysis.query_opportunities[0].cannibalization_score, 0)

    def test_stale_dedicated_page_is_flagged(self):
        query = "ai agent memory"
        url = "https://example.com/agent-memory"
        events = gsc_history(query=query, pages=[url])
        doc = page_doc(
            url,
            title="AI Agent Memory",
            h1=["AI Agent Memory"],
            text="AI agent memory architecture.",
            modified_at=NOW - timedelta(days=500),
        )
        analysis = analyze_gsc_site_content(
            events,
            [doc],
            topic_id="topic:ai-agents",
            query_terms=["ai agent"],
        )
        self.assertGreater(analysis.query_opportunities[0].stale_content_score, 0)

    def test_ctr_gap_uses_site_internal_position_baseline(self):
        target = gsc_history(
            query="ai agent memory",
            pages=["https://example.com/agent-memory"],
            recent_ctr=0.02,
            recent_position=8,
            baseline_position=8,
        )
        benchmark = gsc_history(
            query="unrelated strong query",
            pages=["https://example.com/strong"],
            recent_ctr=0.12,
            baseline_ctr=0.12,
            recent_position=8,
            baseline_position=8,
        )
        doc = page_doc(
            "https://example.com/agent-memory",
            title="AI Agent Memory",
            h1=["AI Agent Memory"],
            text="AI agent memory.",
        )
        analysis = analyze_gsc_site_content(
            [*target, *benchmark],
            [doc],
            topic_id="topic:ai-agents",
            query_terms=["ai agent"],
        )
        row = analysis.query_opportunities[0]
        self.assertIsNotNone(row.baseline_ctr)
        self.assertGreater(row.baseline_ctr, row.actual_ctr)
        self.assertGreater(row.ctr_gap_score, 0)

    def test_pipeline_connects_gsc_page_content_to_supply_gap(self):
        import tempfile

        query = "ai agent memory"
        url = "https://example.com/general"
        events = gsc_history(query=query, pages=[url])
        fetcher = FakeFetcher({
            "https://example.com/robots.txt": ("text/plain", "User-agent: *\nAllow: /\n"),
            url: (
                "text/html",
                """
                <html>
                  <head><title>Company News</title></head>
                  <body><main><h1>Quarterly Update</h1><p>Revenue and hiring news.</p></main></body>
                </html>
                """,
            ),
        })
        crawler = SiteCrawler(
            hydrator=PageHydrator(fetcher),
            robots=RobotsPolicy(fetcher),
            sleep_fn=lambda seconds: None,
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_pipeline(
                topic="AI Agents",
                providers=[StaticGSCProvider(events)],
                gsc_site_url="sc-domain:example.com",
                hydrate_content=True,
                content_crawler=crawler,
                snapshot_path=str(Path(tmp) / "signals.jsonl"),
                cache_path=str(Path(tmp) / "cache.json"),
                content_snapshot_path=str(Path(tmp) / "pages.jsonl"),
            )

        self.assertEqual(report["content_context"]["status"], "complete")
        supply_signals = [
            signal for signal in report["signals"]
            if signal["provider"] == "site_content"
            and signal["signal_type"] == "supply"
        ]
        self.assertEqual(len(supply_signals), 1)
        self.assertLess(supply_signals[0]["normalized_value"], 30)
        self.assertGreater(report["opportunity"]["components"]["supply_gap"], 70)

    def test_page_relevance_is_deterministic(self):
        page = page_doc(
            "https://example.com/agent-memory",
            title="AI Agent Memory",
            h1=["Architecture for Agent Memory"],
            text="Long-term memory for AI agents.",
        )
        first = page_query_relevance(page, "ai agent memory")
        second = page_query_relevance(page, "ai agent memory")
        self.assertEqual(first, second)
        self.assertGreater(first, 50)


if __name__ == "__main__":
    unittest.main()
