"""Deterministic Site Content Graph for first-party and competitor coverage.

This module intentionally stops short of being a full SEO crawler. It hydrates
a bounded set of already-discovered URLs, respects robots.txt, stores semantic
content snapshots, and connects GSC query evidence to actual page content.
"""
from __future__ import annotations

import hashlib
import json
import re
import time as time_module
import urllib.parse
import urllib.robotparser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from core import RawEvent, Signal, clamp, stable_id, utcnow
from gsc import GSCQueryFeatures, summarize_gsc_queries
from web import FetchResponse, fetch_text, parse_datetime


CONTENT_USER_AGENT = "ContentOpportunityRadar"
Fetcher = Callable[..., FetchResponse]


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def normalize_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_meta_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(slots=True)
class PageDocument:
    id: str
    url: str
    canonical_url: str
    domain: str
    title: str
    description: str
    h1: list[str]
    h2: list[str]
    main_text: str
    internal_links: list[str]
    content_hash: str
    retrieved_at: datetime
    published_at: datetime | None = None
    modified_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    previous_content_hash: str | None = None
    changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field_name in (
            "retrieved_at",
            "published_at",
            "modified_at",
            "first_seen_at",
            "last_seen_at",
        ):
            data[field_name] = _iso(getattr(self, field_name))
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageDocument":
        def dt(name: str) -> datetime | None:
            raw = data.get(name)
            if not raw:
                return None
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        return cls(
            id=str(data["id"]),
            url=str(data["url"]),
            canonical_url=str(data["canonical_url"]),
            domain=str(data["domain"]),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            h1=[str(v) for v in data.get("h1") or []],
            h2=[str(v) for v in data.get("h2") or []],
            main_text=str(data.get("main_text") or ""),
            internal_links=[str(v) for v in data.get("internal_links") or []],
            content_hash=str(data.get("content_hash") or ""),
            retrieved_at=dt("retrieved_at") or utcnow(),
            published_at=dt("published_at"),
            modified_at=dt("modified_at"),
            first_seen_at=dt("first_seen_at"),
            last_seen_at=dt("last_seen_at"),
            previous_content_hash=data.get("previous_content_hash"),
            changed=bool(data.get("changed", False)),
        )


class PageSnapshotStore:
    """Append-only content snapshots with content-hash change detection."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._rows: list[PageDocument] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    self._rows.append(PageDocument.from_dict(json.loads(line)))
                except Exception:
                    continue

    def history(self, url: str) -> list[PageDocument]:
        key = normalize_url(url)
        rows = [
            page for page in self._rows
            if normalize_url(page.url) == key or normalize_url(page.canonical_url) == key
        ]
        return sorted(rows, key=lambda page: page.retrieved_at)

    def latest(self, url: str) -> PageDocument | None:
        rows = self.history(url)
        return rows[-1] if rows else None

    def append(self, page: PageDocument) -> PageDocument:
        previous = self.latest(page.canonical_url) or self.latest(page.url)
        page.first_seen_at = (
            previous.first_seen_at or previous.retrieved_at
            if previous
            else page.retrieved_at
        )
        page.last_seen_at = page.retrieved_at
        page.previous_content_hash = previous.content_hash if previous else None
        page.changed = bool(previous and previous.content_hash != page.content_hash)
        self._rows.append(page)

        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(page.to_dict(), ensure_ascii=False) + "\n")
        return page

    def all(self) -> list[PageDocument]:
        return list(self._rows)


class _PageParser(HTMLParser):
    IGNORED = {"script", "style", "noscript", "svg", "canvas", "template"}
    CHROME = {"nav", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.description = ""
        self.canonical = ""
        self.published_at: datetime | None = None
        self.modified_at: datetime | None = None
        self.links: list[str] = []
        self.visible_parts: list[str] = []
        self.main_parts: list[str] = []

        self._ignored_depth = 0
        self._chrome_depth = 0
        self._main_depth = 0
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_d = {name.lower(): (value or "") for name, value in attrs}

        if tag in self.IGNORED:
            self._ignored_depth += 1
            return

        if tag in self.CHROME:
            self._chrome_depth += 1
        if tag in {"main", "article"}:
            self._main_depth += 1
        if tag in {"title", "h1", "h2"} and self._capture_tag is None:
            self._capture_tag = tag
            self._capture_parts = []

        if tag == "a":
            href = attrs_d.get("href", "").strip()
            if href:
                self.links.append(href)

        if tag == "link":
            rel = attrs_d.get("rel", "").casefold().split()
            href = attrs_d.get("href", "").strip()
            if "canonical" in rel and href:
                self.canonical = href

        if tag == "meta":
            key = (
                attrs_d.get("property")
                or attrs_d.get("name")
                or attrs_d.get("itemprop")
                or ""
            ).casefold()
            value = attrs_d.get("content", "").strip()
            if not value:
                return
            if key == "description":
                self.description = value
            elif key in {
                "article:published_time",
                "datepublished",
                "date",
                "publishdate",
                "pubdate",
                "dc.date",
            } and self.published_at is None:
                self.published_at = _parse_meta_date(value)
            elif key in {
                "article:modified_time",
                "datemodified",
                "last-modified",
                "lastmodified",
            } and self.modified_at is None:
                self.modified_at = _parse_meta_date(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in self.IGNORED:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return

        if self._capture_tag == tag:
            value = re.sub(r"\s+", " ", " ".join(self._capture_parts)).strip()
            if value:
                if tag == "title":
                    self.title_parts.append(value)
                elif tag == "h1":
                    self.h1.append(value)
                elif tag == "h2":
                    self.h2.append(value)
            self._capture_tag = None
            self._capture_parts = []

        if tag in {"main", "article"}:
            self._main_depth = max(0, self._main_depth - 1)
        if tag in self.CHROME:
            self._chrome_depth = max(0, self._chrome_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return

        if self._capture_tag is not None:
            self._capture_parts.append(value)

        if not self._chrome_depth:
            self.visible_parts.append(value)
            if self._main_depth:
                self.main_parts.append(value)


def _semantic_hash(title: str, h1: Sequence[str], h2: Sequence[str], text: str) -> str:
    payload = normalize_text(" ".join([title, *h1, *h2, text]))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _internal_links(base_url: str, hrefs: Iterable[str]) -> list[str]:
    base = urllib.parse.urlsplit(base_url)
    rows: set[str] = set()
    for href in hrefs:
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.casefold() != base.netloc.casefold():
            continue
        rows.add(normalize_url(absolute))
        if len(rows) >= 500:
            break
    return sorted(rows)


class PageHydrator:
    def __init__(self, fetcher: Fetcher = fetch_text) -> None:
        self.fetcher = fetcher

    def hydrate(self, url: str) -> PageDocument:
        response = self.fetcher(
            url,
            timeout=15.0,
            accept="text/html,application/xhtml+xml,*/*;q=0.5",
        )
        content_type = response.content_type.casefold()
        if content_type and "html" not in content_type and "xhtml" not in content_type:
            raise ValueError(f"unsupported content type: {response.content_type}")

        parser = _PageParser()
        parser.feed(response.text)

        final_url = normalize_url(response.url)
        canonical = normalize_url(
            urllib.parse.urljoin(final_url, parser.canonical)
            if parser.canonical
            else final_url
        )
        parsed = urllib.parse.urlsplit(canonical)
        title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()
        main_text = " ".join(parser.main_parts)
        if len(main_text) < 100:
            main_text = " ".join(parser.visible_parts)
        main_text = re.sub(r"\s+", " ", main_text).strip()[:200_000]

        retrieved = utcnow()
        return PageDocument(
            id=f"page:{stable_id(canonical)}",
            url=final_url,
            canonical_url=canonical,
            domain=parsed.netloc.casefold(),
            title=title,
            description=parser.description,
            h1=parser.h1[:20],
            h2=parser.h2[:100],
            main_text=main_text,
            internal_links=_internal_links(canonical, parser.links),
            content_hash=_semantic_hash(title, parser.h1, parser.h2, main_text),
            retrieved_at=retrieved,
            published_at=parser.published_at,
            modified_at=parser.modified_at,
            first_seen_at=retrieved,
            last_seen_at=retrieved,
        )


class RobotsPolicy:
    def __init__(
        self,
        fetcher: Fetcher = fetch_text,
        *,
        user_agent: str = CONTENT_USER_AGENT,
    ) -> None:
        self.fetcher = fetcher
        self.user_agent = user_agent
        self._rules: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._delays: dict[str, float] = {}

    @staticmethod
    def origin(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def _load(self, url: str) -> None:
        origin = self.origin(url)
        if origin in self._rules:
            return

        robots_url = origin + "/robots.txt"
        try:
            response = self.fetcher(
                robots_url,
                timeout=10.0,
                accept="text/plain,*/*;q=0.2",
            )
            rules = urllib.robotparser.RobotFileParser()
            rules.set_url(robots_url)
            rules.parse(response.text.splitlines())
            self._rules[origin] = rules
            delay = rules.crawl_delay(self.user_agent)
            if delay is None:
                delay = rules.crawl_delay("*")
            self._delays[origin] = min(float(delay or 0.0), 10.0)
        except Exception:
            # Missing/unreachable robots.txt is treated as no explicit rule.
            self._rules[origin] = None
            self._delays[origin] = 0.0

    def can_fetch(self, url: str) -> bool:
        self._load(url)
        rules = self._rules[self.origin(url)]
        return True if rules is None else rules.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float:
        self._load(url)
        return self._delays.get(self.origin(url), 0.0)


@dataclass(slots=True)
class CrawlReport:
    pages: list[PageDocument]
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, include_text: bool = False) -> dict[str, Any]:
        pages = []
        for page in self.pages:
            item = page.to_dict()
            if not include_text:
                item.pop("main_text", None)
            pages.append(item)
        return {
            "pages": pages,
            "skipped": self.skipped,
            "warnings": self.warnings,
        }


class SiteCrawler:
    """Sequential, robots-aware hydration with a strict page budget."""

    def __init__(
        self,
        *,
        hydrator: PageHydrator | None = None,
        robots: RobotsPolicy | None = None,
        store: PageSnapshotStore | None = None,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        self.hydrator = hydrator or PageHydrator()
        self.robots = robots or RobotsPolicy(self.hydrator.fetcher)
        self.store = store
        self.sleep_fn = sleep_fn

    def hydrate_urls(
        self,
        urls: Sequence[str],
        *,
        max_pages: int = 20,
    ) -> CrawlReport:
        budget = max(0, min(int(max_pages), 100))
        unique: list[str] = []
        seen: set[str] = set()
        for url in urls:
            try:
                normalized = normalize_url(url)
            except Exception:
                continue
            if normalized in seen:
                continue
            parsed = urllib.parse.urlsplit(normalized)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            seen.add(normalized)
            unique.append(normalized)

        pages: list[PageDocument] = []
        skipped: list[str] = []
        warnings: list[str] = []
        last_origin: str | None = None

        for url in unique:
            if len(pages) >= budget:
                skipped.extend(item for item in unique if item not in {p.url for p in pages})
                break

            if not self.robots.can_fetch(url):
                skipped.append(url)
                warnings.append(f"robots.txt disallows: {url}")
                continue

            origin = self.robots.origin(url)
            delay = self.robots.crawl_delay(url)
            if pages and origin == last_origin and delay > 0:
                self.sleep_fn(delay)

            try:
                page = self.hydrator.hydrate(url)
                if self.store:
                    page = self.store.append(page)
                pages.append(page)
                last_origin = origin
            except Exception as exc:
                warnings.append(f"{url}: {type(exc).__name__}: {exc}")

        return CrawlReport(pages=pages, skipped=sorted(set(skipped)), warnings=warnings)


def _token_coverage(field: str, query: str) -> float:
    field_tokens = set(normalize_text(field).split())
    query_tokens = set(normalize_text(query).split())
    if not query_tokens:
        return 0.0
    return len(field_tokens & query_tokens) / len(query_tokens)


def _field_score(field: str, query: str, weight: float) -> float:
    normalized_field = normalize_text(field)
    normalized_query = normalize_text(query)
    if not normalized_field or not normalized_query:
        return 0.0
    if normalized_query in normalized_field:
        return weight
    coverage = _token_coverage(normalized_field, normalized_query)
    if coverage < 0.5:
        return 0.0
    return weight * coverage * 0.85


def page_query_relevance(page: PageDocument, query: str) -> float:
    """Deterministic content coverage score; no LLM/embedding required."""
    parsed = urllib.parse.urlsplit(page.canonical_url)
    url_text = urllib.parse.unquote(parsed.path.replace("-", " ").replace("_", " "))
    score = 0.0
    score += _field_score(page.title, query, 25.0)
    score += max((_field_score(value, query, 25.0) for value in page.h1), default=0.0)
    score += max((_field_score(value, query, 15.0) for value in page.h2), default=0.0)
    score += _field_score(page.description, query, 10.0)
    score += _field_score(url_text, query, 10.0)
    score += _field_score(page.main_text, query, 15.0)
    return clamp(score)


def page_topic_relevance(page: PageDocument, terms: Sequence[str]) -> float:
    return max((page_query_relevance(page, term) for term in terms), default=0.0)


class PageIndex:
    def __init__(self, pages: Sequence[PageDocument]) -> None:
        self.pages = list(pages)
        self._by_url: dict[str, PageDocument] = {}
        for page in pages:
            self._by_url[normalize_url(page.url)] = page
            self._by_url[normalize_url(page.canonical_url)] = page

    def find(self, url: str) -> PageDocument | None:
        try:
            return self._by_url.get(normalize_url(url))
        except Exception:
            return None


def _position_bucket(position: float | None) -> str | None:
    if position is None or position <= 0:
        return None
    if position <= 3:
        return "1-3"
    if position <= 10:
        return "4-10"
    if position <= 20:
        return "11-20"
    if position <= 40:
        return "21-40"
    return "41+"


def _recent_gsc_events(events: Sequence[RawEvent], days: int = 7) -> list[RawEvent]:
    rows = [
        event for event in events
        if event.provider == "gsc" and event.published_at is not None
    ]
    if not rows:
        return []
    latest = max(event.published_at.date() for event in rows)
    start = latest.fromordinal(latest.toordinal() - (days - 1))
    return [event for event in rows if event.published_at.date() >= start]


def internal_ctr_baselines(events: Sequence[RawEvent]) -> dict[str, float]:
    totals: dict[str, list[float]] = {}
    for event in _recent_gsc_events(events):
        impressions = max(0.0, event.metrics.get("impressions", 0.0))
        clicks = max(0.0, event.metrics.get("clicks", 0.0))
        bucket = _position_bucket(event.metrics.get("position"))
        if not bucket or impressions <= 0:
            continue
        pair = totals.setdefault(bucket, [0.0, 0.0])
        pair[0] += clicks
        pair[1] += impressions
    return {
        bucket: clicks / impressions
        for bucket, (clicks, impressions) in totals.items()
        if impressions > 0
    }


@dataclass(slots=True)
class QueryContentOpportunity:
    query: str
    pages: list[str]
    hydrated_page_ids: list[str]
    dedicated_page_ids: list[str]
    best_relevance: float
    query_page_gap_score: float
    cannibalization_score: float
    stale_content_score: float
    ctr_gap_score: float
    actual_ctr: float
    baseline_ctr: float | None
    demand_score: float
    momentum_score: float
    confidence: float
    evidence_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "best_relevance",
            "query_page_gap_score",
            "cannibalization_score",
            "stale_content_score",
            "ctr_gap_score",
            "actual_ctr",
            "baseline_ctr",
            "demand_score",
            "momentum_score",
            "confidence",
        ):
            if data[key] is not None:
                data[key] = round(float(data[key]), 4)
        return data


@dataclass(slots=True)
class SiteContentAnalysis:
    query_opportunities: list[QueryContentOpportunity]
    supply_signal: Signal | None
    requested_page_count: int
    hydrated_page_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_opportunities": [row.to_dict() for row in self.query_opportunities],
            "supply_signal": self.supply_signal.to_dict() if self.supply_signal else None,
            "requested_page_count": self.requested_page_count,
            "hydrated_page_count": self.hydrated_page_count,
        }


def _matches_terms(query: str, terms: Sequence[str]) -> bool:
    normalized_query = normalize_text(query)
    normalized_terms = [normalize_text(term) for term in terms if normalize_text(term)]
    return not normalized_terms or any(term in normalized_query for term in normalized_terms)


def analyze_gsc_site_content(
    gsc_events: Sequence[RawEvent],
    pages: Sequence[PageDocument],
    *,
    topic_id: str,
    query_terms: Sequence[str] = (),
    dedicated_threshold: float = 55.0,
    stale_after_days: int = 180,
) -> SiteContentAnalysis:
    """Connect GSC demand to hydrated page content and produce a Supply signal."""
    features = [
        feature for feature in summarize_gsc_queries(gsc_events)
        if _matches_terms(feature.query, query_terms)
    ]
    page_index = PageIndex(pages)
    ctr_baseline = internal_ctr_baselines(gsc_events)
    recent_events = _recent_gsc_events(gsc_events)
    now = utcnow()

    opportunities: list[QueryContentOpportunity] = []
    requested_urls: set[str] = set()

    for feature in features:
        requested_urls.update(feature.pages)
        hydrated = [
            page for page in (page_index.find(url) for url in feature.pages)
            if page is not None
        ]

        relevance = {
            page.id: page_query_relevance(page, feature.query)
            for page in hydrated
        }
        best_relevance = max(relevance.values(), default=0.0)
        dedicated = sorted([
            page.id for page in hydrated
            if relevance.get(page.id, 0.0) >= dedicated_threshold
        ])

        if hydrated:
            gap_score = clamp(
                feature.demand_score
                * (1.0 - best_relevance / 100.0)
                * (0.8 + 0.2 * feature.momentum_score / 100.0)
            )
        else:
            # Unknown is not the same as a proven gap.
            gap_score = 0.0

        per_page_impressions: dict[str, float] = {}
        for event in recent_events:
            dims = event.raw.get("dimensions") if isinstance(event.raw, dict) else {}
            if str((dims or {}).get("query") or "").casefold() != feature.query.casefold():
                continue
            page_url = str((dims or {}).get("page") or "")
            page = page_index.find(page_url) if page_url else None
            if not page or relevance.get(page.id, 0.0) < 40.0:
                continue
            per_page_impressions[page.id] = (
                per_page_impressions.get(page.id, 0.0)
                + event.metrics.get("impressions", 0.0)
            )

        impression_shares = sorted(per_page_impressions.values(), reverse=True)
        cannibalization = 0.0
        if len(impression_shares) >= 2 and sum(impression_shares) > 0:
            second_share = impression_shares[1] / sum(impression_shares)
            if second_share >= 0.10:
                cannibalization = clamp(
                    feature.demand_score * min(1.0, second_share / 0.40)
                )

        stale_score = 0.0
        if hydrated:
            best_page = max(hydrated, key=lambda page: relevance.get(page.id, 0.0))
            if relevance.get(best_page.id, 0.0) >= dedicated_threshold:
                content_date = best_page.modified_at or best_page.published_at
                if content_date:
                    age_days = max(0, (now - content_date).days)
                    if age_days > stale_after_days:
                        age_factor = min(
                            1.0,
                            0.25 + (age_days - stale_after_days) / 365.0,
                        )
                        stale_score = clamp(feature.demand_score * age_factor)

        bucket = _position_bucket(feature.recent_position)
        baseline_ctr = ctr_baseline.get(bucket) if bucket else None
        ctr_gap_score = 0.0
        if baseline_ctr and baseline_ctr > feature.recent_ctr:
            relative_gap = (baseline_ctr - feature.recent_ctr) / baseline_ctr
            ctr_gap_score = clamp(feature.demand_score * relative_gap)

        page_evidence = [page.id for page in hydrated]
        evidence = sorted({*feature.evidence_ids, *page_evidence})
        hydration_ratio = (
            len(hydrated) / len(feature.pages)
            if feature.pages else 0.0
        )
        confidence = feature.confidence * hydration_ratio if hydrated else 0.0

        opportunities.append(
            QueryContentOpportunity(
                query=feature.query,
                pages=feature.pages,
                hydrated_page_ids=sorted(page_evidence),
                dedicated_page_ids=dedicated,
                best_relevance=best_relevance,
                query_page_gap_score=gap_score,
                cannibalization_score=cannibalization,
                stale_content_score=stale_score,
                ctr_gap_score=ctr_gap_score,
                actual_ctr=feature.recent_ctr,
                baseline_ctr=baseline_ctr,
                demand_score=feature.demand_score,
                momentum_score=feature.momentum_score,
                confidence=confidence,
                evidence_ids=evidence,
            )
        )

    known = [row for row in opportunities if row.hydrated_page_ids]
    supply_signal: Signal | None = None
    if known:
        feature_map = {feature.query: feature for feature in features}
        weights = [
            max(feature_map[row.query].recent_impressions_per_day, 0.1)
            for row in known
        ]
        total_weight = sum(weights)
        supply_score = sum(
            row.best_relevance * weight
            for row, weight in zip(known, weights)
        ) / total_weight
        confidence = sum(
            row.confidence * weight
            for row, weight in zip(known, weights)
        ) / total_weight
        evidence_ids = sorted({
            evidence
            for row in known
            for evidence in row.evidence_ids
        })
        source = pages[0].domain if pages else "site-content"
        observed_at = max(
            [page.retrieved_at for page in pages]
            + [
                event.published_at or event.retrieved_at
                for event in gsc_events
                if event.provider == "gsc"
            ],
            default=now,
        )

        supply_signal = Signal(
            id=stable_id("site-content-supply", topic_id, source),
            topic_id=topic_id,
            entity_ids=[f"domain:{stable_id(source)}"],
            signal_type="supply",
            source=source,
            provider="site_content",
            observed_at=observed_at,
            value=supply_score,
            normalized_value=clamp(supply_score),
            confidence=clamp(confidence),
            evidence_ids=evidence_ids,
            freshness_seconds=max(0, int((now - observed_at).total_seconds())),
        )

    return SiteContentAnalysis(
        query_opportunities=sorted(
            opportunities,
            key=lambda row: (
                row.query_page_gap_score
                + row.cannibalization_score
                + row.stale_content_score
                + row.ctr_gap_score
            ),
            reverse=True,
        ),
        supply_signal=supply_signal,
        requested_page_count=len(requested_urls),
        hydrated_page_count=len(pages),
    )
