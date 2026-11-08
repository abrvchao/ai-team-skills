"""新词算法离线测试，不访问 GitHub/Trends/付费模型。"""
import unittest
from datetime import date, timedelta
from emerging_terms_experiment import TermMention, detect_emerging_terms, settled_trend

TODAY = date(2026, 10, 13)

class EmergingTermsExperimentTests(unittest.TestCase):
    def test_feature_disabled_by_default(self):
        self.assertEqual(detect_emerging_terms([], today=TODAY), [])

    def test_independent_sources_not_repeated_posts(self):
        inputs = [TermMention("Qwen 8", "account-a", TODAY)] * 8
        self.assertEqual(detect_emerging_terms(inputs, today=TODAY, enabled=True), [])
        inputs.append(TermMention("Qwen 8", "account-b", TODAY))
        rows = detect_emerging_terms(inputs, today=TODAY, enabled=True)
        self.assertEqual(rows[0]["sources"], 2)
        self.assertEqual(rows[0]["status"], "experimental")

    def test_old_baseline_dampens_false_surge(self):
        inputs = [TermMention("old brand", f"old-{i}", TODAY - timedelta(days=10))
                  for i in range(100)]
        inputs += [TermMention("old brand", f"new-{i}", TODAY) for i in range(2)]
        self.assertEqual(detect_emerging_terms(inputs, today=TODAY, enabled=True), [])

    def test_unsettled_points_ignored(self):
        points = [(TODAY - timedelta(days=3), 5),
                  (TODAY - timedelta(days=1), 100), (TODAY, 0)]
        self.assertEqual(settled_trend(points, today=TODAY), points[:1])

if __name__ == "__main__":
    unittest.main()
