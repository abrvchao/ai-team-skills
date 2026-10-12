"""Dashboard v2 offline contract regression: no Bridge, device or network required."""
from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "dashboard-next"


class DashboardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.scripts: list[dict[str, str]] = []
        self.stylesheets: list[str] = []
        self.lang_options: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if data.get("id"):
            self.ids.add(data["id"])
        if tag == "script":
            self.scripts.append({k: v or "" for k, v in attrs})
        if tag == "link" and data.get("rel") == "stylesheet":
            self.stylesheets.append(str(data.get("href") or ""))
        if tag == "option":
            self.lang_options.add(str(data.get("value") or ""))


class DashboardV2ContractTests(unittest.TestCase):
    def test_local_assets_and_bilingual_contract_present(self) -> None:
        markup = (ROOT / "index.html").read_text(encoding="utf-8")
        parser = DashboardParser()
        parser.feed(markup)
        self.assertTrue(
            {"stats", "projects", "workers", "sources", "connectivity", "updated",
             "filters", "refresh", "lang"}.issubset(parser.ids)
        )
        self.assertTrue({"zh", "en", "ja", "ko"}.issubset(parser.lang_options))
        self.assertEqual(parser.stylesheets, ["styles.css"])
        self.assertEqual([s.get("src") for s in parser.scripts], ["data.js", "app.js"])
        self.assertTrue(all("src" in s and "http" not in s["src"] for s in parser.scripts))
        self.assertIn("Content-Security-Policy", markup)

    def test_all_assets_exist_and_no_external_js_or_credentials(self) -> None:
        for path in ("index.html", "styles.css", "data.js", "app.js", "test_data.js"):
            self.assertTrue((ROOT / path).is_file(), path)
        js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('jsonRequest("/api/monitor")', js)
        self.assertIn('jsonRequest("/api/status")', js)
        self.assertIn("Promise.allSettled", js)
        self.assertIn("AbortController", js)
        self.assertIn("textContent", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("--dangerously-skip-permissions", js)
        self.assertNotIn("https://", js)
        self.assertIn("state.monitorError", js)
        self.assertIn("bridge_available === true", js)

    def test_no_unverified_completion_in_copy(self) -> None:
        js = (ROOT / "data.js").read_text(encoding="utf-8")
        self.assertIn("if (typeof v !== \"number\"", js)
        self.assertIn("data.bridge_available === true", js)
        self.assertIn('progress: percent(p && p.percent)', js)


if __name__ == "__main__":
    unittest.main()
