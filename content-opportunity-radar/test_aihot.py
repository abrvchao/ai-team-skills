from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from aihot import AIHOTProvider
from core import CollectionRequest, CollectionResult, ProviderState
from radar import collect_seed_events


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class AIHOTProviderTests(unittest.TestCase):
    def test_normalizes_aihot_item_as_experimental_raw_event(self):
        payload = {
            "items": [{
                "id": "a-1",
                "title": "Agent memory gets easier",
                "summary": "A practical agent-memory release",
                "source": {"name": "Example"},
                "links": {
                    "aihot": "https://aihot.news/item/a-1",
                    "original": "https://example.com/a-1",
                },
                "publishedAt": "2026-09-29T01:00:00Z",
                "discoveredAt": "2026-09-29T02:00:00Z",
                "category": "ai-products",
                "score": 88,
                "selected": True,
                "reason": "high signal",
            }]
        }
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            result = AIHOTProvider().collect(CollectionRequest(topic="AI Agents", limit=5))
        self.assertEqual(result.status, ProviderState.HEALTHY)
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event.provider, "aihot")
        self.assertEqual(event.metrics["aihot_score"], 88.0)
        self.assertTrue(event.raw["experimental"])
        self.assertEqual(event.provenance.terms_class, "experimental_personal_research")

    def test_aihot_seed_mode_is_opt_in(self):
        with patch.object(
            AIHOTProvider,
            "collect",
            return_value=CollectionResult("aihot", ProviderState.HEALTHY, events=[]),
        ):
            result = collect_seed_events(scope="AI Agents", seed_mode="aihot")
        self.assertEqual(set(result.providers), {"aihot"})
        self.assertEqual(result.source_mode, "live:aihot")
