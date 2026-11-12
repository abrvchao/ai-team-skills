from __future__ import annotations

import unittest
from unittest.mock import patch

from core import CollectionRequest, ProviderState
from pipeline import GDELTProvider, GDELT_REQUEST_TIMEOUT_SECONDS


class GDELTTimeoutTests(unittest.TestCase):
    def test_gdelt_uses_a_bounded_timeout_for_sequential_radar_scans(self):
        with patch(
            "pipeline._json_request",
            return_value=({"articles": []}, {}),
        ) as request:
            result = GDELTProvider().collect(
                CollectionRequest(topic="AI Agents", limit=5)
            )

        self.assertEqual(
            GDELT_REQUEST_TIMEOUT_SECONDS,
            request.call_args.kwargs["timeout"],
        )
        self.assertLessEqual(GDELT_REQUEST_TIMEOUT_SECONDS, 5.0)
        self.assertEqual(ProviderState.DEGRADED, result.status)

    def test_gdelt_timeout_degrades_provider_without_aborting_scan(self):
        with patch(
            "pipeline._json_request",
            side_effect=TimeoutError("synthetic timeout"),
        ):
            result = GDELTProvider().collect(
                CollectionRequest(topic="AI Agents", limit=5)
            )

        self.assertEqual(ProviderState.DEGRADED, result.status)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
