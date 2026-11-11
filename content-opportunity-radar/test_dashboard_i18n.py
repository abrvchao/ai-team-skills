from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class DashboardI18nTests(unittest.TestCase):
    def _packs(self) -> dict:
        script = r"""
const fs = require("fs");
const vm = require("vm");
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync("dashboard/i18n.js", "utf8"), sandbox);
process.stdout.write(JSON.stringify(sandbox.window.RadarI18n.packs));
"""
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_all_eight_languages_have_complete_ui_dictionary(self):
        packs = self._packs()
        self.assertEqual(set(packs), {"zh", "en", "ja", "ko", "fr", "de", "es", "pt"})
        canonical = set(packs["en"])
        self.assertGreaterEqual(len(canonical), 75)
        for language, pack in packs.items():
            with self.subTest(language=language):
                self.assertEqual(set(pack), canonical)
                self.assertTrue(all(str(value).strip() for value in pack.values()))

    def test_non_english_packs_do_not_fallback_on_core_dashboard_surfaces(self):
        packs = self._packs()
        keys = (
            "heroTitle",
            "scoreBreakdown",
            "providerNoState",
            "detailLoadFailed",
            "scanFailedMessage",
        )
        for language in ("zh", "ja", "ko", "fr", "de", "es", "pt"):
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(packs[language][key], packs["en"][key])

    def test_production_dashboard_must_not_load_third_party_javascript(self):
        # 重要：页面可能含 GSC 站点和业务数据，不能加载开发期 Figma 捕获脚本。
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("mcp.figma.com/mcp/html-to-design/capture.js", html)
        self.assertFalse("https://mcp.figma.com/" in html)
        self.assertIn('<script src="/i18n.js" defer>', html)
        self.assertIn('<script src="/app.js" defer>', html)

    def test_dashboard_uses_shared_i18n_for_static_and_dynamic_content(self):
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertLess(html.index('/i18n.js'), html.index('/app.js'))
        self.assertGreaterEqual(html.count("data-i18n="), 25)
        self.assertNotIn("const UI_TEXT", app)
        self.assertIn("const I18N = window.RadarI18n", app)
        self.assertIn("I18N.applyStatic(document, state.language)", app)
        self.assertIn("I18N.locale(state.language)", app)
        for stale in (
            '"No source summary available."',
            '"No history yet."',
            '"Open source ↗"',
            '"No explanation recorded."',
            '"Unable to load opportunity"',
        ):
            self.assertNotIn(stale, app)


if __name__ == "__main__":
    unittest.main()
