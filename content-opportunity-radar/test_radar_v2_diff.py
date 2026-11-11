"""Radar V2 变化检测器单元测试。

覆盖任务要求：new、removed、rank 变化、score 漂移、重复 ID、确定性排序。
测试只使用内存 dict，不接触 V1 数据库（`.radar/radar.db`），不修改任何 V1 文件。

运行：
    python3 -m unittest test_radar_v2_diff -v
"""
from __future__ import annotations

import unittest

import radar_v2_diff as diff


def item(topic_id, rank, score=None, name=None, nested_scores=False):
    """构造一条机会快照记录。nested_scores=True 时使用 V1 的 scores.opportunity 结构。"""
    row = {"topic_id": topic_id, "rank": rank}
    if name is not None:
        row["topic_name"] = name
    else:
        row["topic_name"] = topic_id.upper()
    if score is not None:
        if nested_scores:
            row["scores"] = {"opportunity": score}
        else:
            row["opportunity_score"] = score
    return row


def by_type(diffs, change_type):
    return [d for d in diffs if d["change_type"] == change_type]


class NewAndRemovedTests(unittest.TestCase):
    def test_new_detected(self):
        prev = [item("T1", 1, 50.0)]
        curr = [item("T1", 1, 50.0), item("T2", 2, 40.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        news = by_type(diffs, diff.NEW)

        self.assertEqual([d["topic_id"] for d in news], ["T2"])
        self.assertIsNone(news[0]["prev_rank"])
        self.assertEqual(news[0]["curr_rank"], 2)
        self.assertEqual(news[0]["magnitude"], 100.0)

    def test_removed_detected(self):
        prev = [item("T1", 1, 50.0), item("T2", 2, 40.0)]
        curr = [item("T1", 1, 50.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        removed = by_type(diffs, diff.REMOVED)

        self.assertEqual([d["topic_id"] for d in removed], ["T2"])
        self.assertEqual(removed[0]["prev_rank"], 2)
        self.assertIsNone(removed[0]["curr_rank"])
        self.assertEqual(removed[0]["magnitude"], 100.0)
        # 别名与设计文档 disappeared 等价。
        self.assertEqual(diff.DISAPPEARED, diff.REMOVED)

    def test_no_change_returns_empty(self):
        rows = [item("T1", 1, 50.0), item("T2", 2, 40.0)]
        diffs = diff.detect_changes(rows, list(rows), prev_run_id="run-1", curr_run_id="run-2")
        self.assertEqual(diffs, [])

    def test_bootstrap_first_run_marks_all_new_with_zero_magnitude(self):
        curr = [item("T1", 1, 50.0), item("T2", 2, 40.0)]

        diffs = diff.detect_changes([], curr, prev_run_id=None, curr_run_id="run-1")
        news = by_type(diffs, diff.NEW)

        self.assertEqual(len(news), 2)
        self.assertTrue(all(d["magnitude"] == 0.0 for d in news))
        self.assertTrue(all(d["details"]["bootstrap"] is True for d in news))


class RankChangeTests(unittest.TestCase):
    def test_rank_up_and_rank_down(self):
        prev = [
            item("UP", 10, 50.0),
            item("DOWN", 3, 50.0),
            item("STAY", 5, 50.0),
        ]
        curr = [
            item("UP", 3, 50.0),
            item("DOWN", 12, 50.0),
            item("STAY", 5, 50.0),
        ]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        up = by_type(diffs, diff.RANK_UP)
        down = by_type(diffs, diff.RANK_DOWN)
        self.assertEqual([d["topic_id"] for d in up], ["UP"])
        self.assertEqual(up[0]["rank_delta"], 7)
        self.assertEqual(up[0]["magnitude"], 17.5)  # 7 / 20 * 50
        self.assertEqual([d["topic_id"] for d in down], ["DOWN"])
        self.assertEqual(down[0]["rank_delta"], -9)
        self.assertEqual(down[0]["magnitude"], 22.5)  # 9 / 20 * 50

    def test_rank_threshold_is_inclusive_and_small_moves_ignored(self):
        prev = [item("BOUNDARY", 5, 50.0), item("SMALL", 8, 50.0)]
        curr = [item("BOUNDARY", 3, 50.0), item("SMALL", 9, 50.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        self.assertEqual([d["topic_id"] for d in by_type(diffs, diff.RANK_UP)], ["BOUNDARY"])
        self.assertEqual(by_type(diffs, diff.RANK_DOWN), [])
        self.assertEqual(by_type(diffs, diff.RANK_UP)[0]["rank_delta"], 2)

    def test_custom_rank_threshold(self):
        prev = [item("T1", 5, 50.0)]
        curr = [item("T1", 6, 50.0)]
        self.assertEqual(
            diff.detect_changes(prev, curr, prev_run_id="r1", curr_run_id="r2"),
            [],
        )
        diffs = diff.detect_changes(
            prev, curr, rank_threshold=1, prev_run_id="r1", curr_run_id="r2"
        )
        self.assertEqual(len(by_type(diffs, diff.RANK_DOWN)), 1)


class ScoreDriftTests(unittest.TestCase):
    def test_score_up_and_score_down(self):
        prev = [item("UP", 1, 50.0), item("DOWN", 2, 60.0)]
        curr = [item("UP", 1, 61.0), item("DOWN", 2, 49.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        up = by_type(diffs, diff.SCORE_UP)
        down = by_type(diffs, diff.SCORE_DOWN)
        self.assertEqual([d["topic_id"] for d in up], ["UP"])
        self.assertEqual(up[0]["score_delta"], 11.0)
        self.assertEqual(up[0]["magnitude"], 11.0)
        self.assertEqual([d["topic_id"] for d in down], ["DOWN"])
        self.assertEqual(down[0]["score_delta"], -11.0)

    def test_score_threshold_is_inclusive_and_small_drift_ignored(self):
        prev = [item("BOUNDARY", 1, 50.0), item("SMALL", 2, 50.0)]
        curr = [item("BOUNDARY", 1, 55.0), item("SMALL", 2, 54.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        self.assertEqual([d["topic_id"] for d in by_type(diffs, diff.SCORE_UP)], ["BOUNDARY"])
        self.assertEqual(by_type(diffs, diff.SCORE_DOWN), [])

    def test_missing_score_on_one_side_skips_score_diff(self):
        prev = [{"topic_id": "T1", "topic_name": "T1", "rank": 1}]
        curr = [item("T1", 1, 99.0)]
        diffs = diff.detect_changes(prev, curr, prev_run_id="r1", curr_run_id="r2")
        self.assertEqual(by_type(diffs, diff.SCORE_UP), [])

    def test_same_topic_can_emit_rank_and_score_diffs(self):
        prev = [item("T1", 10, 40.0)]
        curr = [item("T1", 2, 70.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        self.assertEqual(len(diffs), 2)
        self.assertEqual({d["change_type"] for d in diffs}, {diff.RANK_UP, diff.SCORE_UP})
        # 幅度排序：score_up(30) 在 rank_up(20) 之前。
        self.assertEqual([d["change_type"] for d in diffs], [diff.SCORE_UP, diff.RANK_UP])

    def test_nested_v1_scores_shape_is_supported(self):
        prev = [item("T1", 1, 50.0, nested_scores=True)]
        curr = [item("T1", 1, 62.0, nested_scores=True)]
        diffs = diff.detect_changes(prev, curr, prev_run_id="r1", curr_run_id="r2")
        self.assertEqual(len(by_type(diffs, diff.SCORE_UP)), 1)


class TopNWindowTests(unittest.TestCase):
    def test_only_top_n_are_compared(self):
        prev = [item("A", 2, 50.0)]
        curr = [
            item("B", 1, 50.0),
            item("C", 2, 50.0),
            item("A", 3, 50.0),
        ]

        diffs = diff.detect_changes(
            prev, curr, top_n=2, prev_run_id="run-1", curr_run_id="run-2"
        )

        self.assertEqual({d["topic_id"] for d in by_type(diffs, diff.NEW)}, {"B", "C"})
        # A 从窗口内(prev rank2)跌出 top-2，按窗口语义记为 removed，而不是 rank_down。
        self.assertEqual([d["topic_id"] for d in by_type(diffs, diff.REMOVED)], ["A"])


class DuplicateIdTests(unittest.TestCase):
    def test_duplicate_in_curr_is_reported_and_best_rank_kept(self):
        curr = [
            item("T1", 5, 10.0),
            item("T1", 2, 20.0),
            item("T2", 1, 30.0),
        ]

        diffs = diff.detect_changes([], curr, prev_run_id="run-1", curr_run_id="run-2")

        duplicates = by_type(diffs, diff.DUPLICATE_ID)
        self.assertEqual(len(duplicates), 1)
        duplicate = duplicates[0]
        self.assertEqual(duplicate["topic_id"], "T1")
        self.assertEqual(duplicate["curr_rank"], 2)  # 保留名次最好的那条
        self.assertEqual(duplicate["details"]["side"], "curr")
        self.assertEqual(duplicate["details"]["duplicate_count"], 2)
        self.assertEqual(duplicate["details"]["kept_rank"], 2)
        self.assertEqual(duplicate["details"]["dropped_ranks"], [5])
        self.assertEqual(duplicate["magnitude"], 0.0)

        # new diff 使用去重后的规范行，不出现两条 T1。
        news = by_type(diffs, diff.NEW)
        self.assertEqual([d["topic_id"] for d in news], ["T1", "T2"])
        self.assertEqual(news[0]["curr_rank"], 2)

    def test_duplicate_in_prev_side_reported(self):
        prev = [item("T1", 7, 10.0), item("T1", 3, 20.0)]
        curr = [item("T1", 3, 20.0)]

        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        duplicates = by_type(diffs, diff.DUPLICATE_ID)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["details"]["side"], "prev")
        self.assertEqual(duplicates[0]["details"]["kept_rank"], 3)
        self.assertEqual(duplicates[0]["prev_rank"], 3)

    def test_duplicate_handling_is_order_independent(self):
        curr = [
            item("T1", 5, 10.0),
            item("T2", 1, 30.0),
            item("T1", 2, 20.0),
        ]
        forward = diff.detect_changes([], curr, prev_run_id="run-1", curr_run_id="run-2")
        backward = diff.detect_changes(
            [], list(reversed(curr)), prev_run_id="run-1", curr_run_id="run-2"
        )
        self.assertEqual(forward, backward)

    def test_missing_topic_id_or_rank_raises(self):
        with self.assertRaises(ValueError):
            diff.detect_changes([], [{"rank": 1}], curr_run_id="run-2")
        with self.assertRaises(ValueError):
            diff.detect_changes([], [{"topic_id": "T1"}], curr_run_id="run-2")


class DeterministicOrderingTests(unittest.TestCase):
    def _scenario(self):
        prev = [
            item("UP", 10, 40.0),
            item("DOWN", 2, 80.0),
            item("GONE", 4, 30.0),
            item("FLAT", 6, 50.0),
        ]
        curr = [
            item("UP", 1, 70.0),
            item("DOWN", 9, 60.0),
            item("NEW", 3, 65.0),
            item("FLAT", 6, 50.0),
        ]
        return prev, curr

    def test_output_is_sorted_by_magnitude_desc_then_type_topic(self):
        prev, curr = self._scenario()
        diffs = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")

        magnitudes = [d["magnitude"] for d in diffs]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))

        # 全局排序键与实现契约一致：magnitude 降序 → 类型优先级 → topic_id。
        order = {c: i for i, c in enumerate(diff.CHANGE_TYPES)}
        keys = [
            (-d["magnitude"], order[d["change_type"]], d["topic_id"]) for d in diffs
        ]
        self.assertEqual(keys, sorted(keys))

    def test_same_input_produces_identical_output(self):
        prev, curr = self._scenario()
        first = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        second = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        self.assertEqual(first, second)

    def test_input_order_does_not_change_output(self):
        prev, curr = self._scenario()
        forward = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        shuffled = diff.detect_changes(
            list(reversed(prev)),
            [curr[2], curr[0], curr[3], curr[1]],
            prev_run_id="run-1",
            curr_run_id="run-2",
        )
        self.assertEqual(forward, shuffled)

    def test_diff_id_is_stable_and_run_scoped(self):
        prev, curr = self._scenario()
        first = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        second = diff.detect_changes(prev, curr, prev_run_id="run-1", curr_run_id="run-2")
        self.assertEqual([d["diff_id"] for d in first], [d["diff_id"] for d in second])
        self.assertTrue(all(len(d["diff_id"]) == 24 for d in first))

        changed_run = diff.detect_changes(
            prev, curr, prev_run_id="run-1", curr_run_id="run-3"
        )
        self.assertNotEqual(
            [d["diff_id"] for d in first], [d["diff_id"] for d in changed_run]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
