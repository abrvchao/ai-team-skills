"""Website source discovery and monitoring provider using only Python stdlib."""
from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

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


USER_AGENT = "ContentOpportunityRadar/0.1 (+https://github.com/abrvchao/ai-team-skills)"
COMMON_FEEDS = (
    "/feed", "/feed.xml", "/rss", "/rss.xml", "/atom.xml",
    "/blog/feed", "/blog/feed.xml", "/news/rss", "/changelog/rss",
)
COMMON_SITEMAPS = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml")


@dataclass(slots=True)
class FetchResponse:
    url: str
    status: int
    content_type: str
    text: str


def fetch_text(url: str, *, timeout: float = 12.0, accept: str = "*/*") -> FetchResponse:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError("response larger than 5 MiB")
        charset = response.headers.get_content_charset() or "utf-8"
        return FetchResponse(
            url=response.geturl(),
            status=getattr(response, "status", 200),
            content_type=response.headers.get("Content-Type", ""),
            text=data.decode(charset, errors="replace"),
        )


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.h1 = ""
        self.description = ""
        self.canonical = ""
        self.feeds: list[tuple[str, str]] = []
        self._tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag in {"title", "h1"}:
            self._tag = tag
        if tag == "meta" and attrs_d.get("name", "").lower() == "description":
            self.description = attrs_d.get("content", "")
        if tag == "link":
            rel = attrs_d.get("rel", "").lower().split()
            href = attrs_d.get("href", "")
            type_ = attrs_d.get("type", "").lower()
            if "canonical" in rel and href:
                self.canonical = href
            if "alternate" in rel and href and ("rss" in type_ or "atom" in type_):
                self.feeds.append((href, "atom" if "atom" in type_ else "rss"))

    def handle_endtag(self, tag: str) -> None:
        if self._tag == tag.lower():
            self._tag = None

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return
        if self._tag == "title":
            self.title = (self.title + " " + value).strip()
        elif self._tag == "h1" and not self.h1:
            self.h1 = value


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str, source_url: str, limit: int = 20) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, Any] = {"source_url": source_url}
        links: list[str] = []
        for child in list(node):
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                fields["title"] = text
            elif name in {"description", "summary", "content"} and text:
                fields.setdefault("text", re.sub(r"<[^>]+>", " ", text))
            elif name in {"pubdate", "published", "updated"} and text:
                fields.setdefault("published_at", parse_datetime(text))
            elif name == "link":
                href = child.attrib.get("href")
                links.append(href or text)
            elif name in {"guid", "id"} and text:
                fields["external_id"] = text
        fields["url"] = next((u for u in links if u), fields.get("external_id"))
        if fields.get("title") and fields.get("url"):
            items.append(fields)
        if len(items) >= limit:
            break
    return items


def parse_sitemap(xml_text: str, limit: int = 100) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    rows: list[dict[str, Any]] = []
    for node in root.iter():
        if _local_name(node.tag) != "url":
            continue
        loc = None
        lastmod = None
        for child in list(node):
            name = _local_name(child.tag)
            if name == "loc":
                loc = (child.text or "").strip()
            elif name == "lastmod":
                lastmod = parse_datetime((child.text or "").strip())
        if loc:
            rows.append({"url": loc, "published_at": lastmod, "title": urllib.parse.urlparse(loc).path})
        if len(rows) >= limit:
            break
    return rows


def parse_sitemap_index(xml_text: str, limit: int = 10) -> list[str]:
    root = ET.fromstring(xml_text)
    rows: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) != "sitemap":
            continue
        for child in list(node):
            if _local_name(child.tag) == "loc":
                loc = (child.text or "").strip()
                if loc:
                    rows.append(loc)
                    break
        if len(rows) >= limit:
            break
    return rows


def _looks_like_feed(response: FetchResponse) -> bool:
    sample = response.text[:4000].lower()
    ctype = response.content_type.lower()
    return (
        "rss" in ctype
        or "atom" in ctype
        or "<rss" in sample
        or "<feed" in sample
        or "<rdf:rdf" in sample
    )


def _looks_like_sitemap(response: FetchResponse) -> bool:
    sample = response.text[:4000].lower()
    return "<urlset" in sample or "<sitemapindex" in sample


def discover_source(url: str) -> dict[str, str]:
    """Return best monitorable source without requiring browser automation."""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    page: FetchResponse | None = None
    try:
        page = fetch_text(url, accept="text/html,application/xhtml+xml,*/*;q=0.8")
        parser = HeadParser()
        parser.feed(page.text)
        for href, type_ in parser.feeds:
            candidate = urllib.parse.urljoin(page.url, href)
            try:
                response = fetch_text(candidate, accept="application/rss+xml,application/atom+xml,application/xml,*/*;q=0.5")
                if _looks_like_feed(response):
                    return {"url": response.url, "type": type_, "method": "html-link"}
            except Exception:
                pass
    except Exception:
        page = None

    for path in COMMON_FEEDS:
        candidate = urllib.parse.urljoin(origin, path)
        try:
            response = fetch_text(candidate, accept="application/rss+xml,application/atom+xml,application/xml,*/*;q=0.5")
            if _looks_like_feed(response):
                return {
                    "url": response.url,
                    "type": "atom" if "<feed" in response.text[:1000].lower() else "rss",
                    "method": "common-path",
                }
        except Exception:
            continue

    robot_sitemaps: list[str] = []
    try:
        robots = fetch_text(urllib.parse.urljoin(origin, "/robots.txt"), accept="text/plain,*/*;q=0.5")
        for line in robots.text.splitlines():
            match = re.match(r"\s*Sitemap\s*:\s*(.+)\s*$", line, re.I)
            if match:
                robot_sitemaps.append(urllib.parse.urljoin(origin, match.group(1).strip()))
    except Exception:
        pass

    for candidate in [*robot_sitemaps, *(urllib.parse.urljoin(origin, p) for p in COMMON_SITEMAPS)]:
        try:
            response = fetch_text(candidate, accept="application/xml,text/xml,*/*;q=0.5")
            if _looks_like_sitemap(response):
                return {"url": response.url, "type": "sitemap", "method": "robots-or-common"}
        except Exception:
            continue

    return {
        "url": page.url if page else url,
        "type": "webpage",
        "method": "fallback",
    }


class WebsiteProvider(DataProvider):
    id = "website"
    source = "website"
    primary = "RSS/Atom"
    fallback = "Sitemap -> robots.txt sitemap -> webpage watch"
    terms_class = "public_web"
    staleness_ttl_seconds = 21600

    def collect(self, request: CollectionRequest) -> CollectionResult:
        target = str(request.metadata.get("url") or "").strip()
        if not target:
            return CollectionResult(
                provider=self.id,
                status=ProviderState.DISABLED,
                warnings=["request.metadata.url is required"],
            )

        discovered = discover_source(target)
        response = fetch_text(discovered["url"])
        now = utcnow()
        rows: list[dict[str, Any]]

        if discovered["type"] in {"rss", "atom"}:
            rows = parse_feed(response.text, response.url, request.limit)
            method = AcquisitionMethod.ATOM if discovered["type"] == "atom" else AcquisitionMethod.RSS
        elif discovered["type"] == "sitemap":
            rows = parse_sitemap(response.text, request.limit)
            if not rows and "<sitemapindex" in response.text[:4000].lower():
                rows = []
                for child_url in parse_sitemap_index(response.text, limit=5):
                    try:
                        child = fetch_text(
                            child_url,
                            accept="application/xml,text/xml,*/*;q=0.5",
                        )
                        rows.extend(parse_sitemap(child.text, request.limit - len(rows)))
                    except Exception:
                        continue
                    if len(rows) >= request.limit:
                        break
            method = AcquisitionMethod.PUBLIC_WEB_API
        else:
            parser = HeadParser()
            parser.feed(response.text)
            resolved = parser.canonical and urllib.parse.urljoin(response.url, parser.canonical) or response.url
            content_hash = hashlib.sha256(response.text.encode("utf-8")).hexdigest()
            rows = [{
                "url": resolved,
                "title": parser.title or parser.h1 or urllib.parse.urlparse(resolved).netloc,
                "text": parser.description,
                "external_id": content_hash,
                "published_at": None,
            }]
            method = AcquisitionMethod.HTML

        events: list[RawEvent] = []
        for row in rows:
            event_url = row.get("url")
            title = row.get("title") or event_url or "website update"
            events.append(
                RawEvent(
                    id=stable_id(self.id, event_url, row.get("external_id"), title),
                    provider=self.id,
                    source=urllib.parse.urlparse(target if re.match(r"^https?://", target) else "https://" + target).netloc,
                    acquisition_method=method,
                    external_id=str(row.get("external_id") or event_url or ""),
                    url=event_url,
                    title=title,
                    text=row.get("text"),
                    published_at=row.get("published_at"),
                    retrieved_at=now,
                    metrics={"content_count": 1.0},
                    raw={"discovered": discovered, "row": row},
                    provenance=Provenance(
                        terms_class=self.terms_class,
                        endpoint=discovered["url"],
                    ),
                )
            )

        status = ProviderState.HEALTHY if events else ProviderState.DEGRADED
        return CollectionResult(
            provider=self.id,
            status=status,
            events=events,
            warnings=[] if events else ["source discovered but no usable items parsed"],
        )
