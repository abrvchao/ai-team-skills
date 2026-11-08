"""R3 human-label validation batch（R3 人工标注验证批次）."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

from candidate_eval import SCHEMA_VERSION, score_labels


BATCH_SCHEMA_VERSION = "r3-validation-batch-v1"
RESULT_SCHEMA_VERSION = "r3-validation-result-v1"
GRADED_RELEVANCE_SCORES = {
    "strong": 1.0,
    "medium": 2 / 3,
    "weak": 1 / 3,
    "unrelated": 0.0,
}


def _graded_relevance_score(item: Mapping[str, Any]) -> float | None:
    level = item.get("relevance_level")
    if level in GRADED_RELEVANCE_SCORES:
        return float(GRADED_RELEVANCE_SCORES[str(level)])
    if level == "uncertain":
        return None
    # 中文说明：旧版二元标签没有“强/中/弱”信息，不能事后推断 graded relevance（分级相关性）。
    return None


def build_batch(documents: list[Mapping[str, Any]], *, source_paths: list[str] | None = None) -> dict[str, Any]:
    if not documents:
        raise ValueError("at least one candidate-eval document is required")
    queues = []
    for index, document in enumerate(documents):
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"queue {index + 1} must use {SCHEMA_VERSION}")
        queues.append({
            "scope": document.get("scope"),
            "workspace_id": document.get("workspace_id"),
            "run_id": document.get("run_id"),
            "source_path": (source_paths or [None] * len(documents))[index],
            "document": dict(document),
        })
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "gate": {
            "minimum_labeled_opportunities": 20,
            "minimum_unique_labeled_topics": 20,
            "k": 5,
            "minimum_precision_at_k": 0.60,
        },
        "queues": queues,
    }


def score_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    if batch.get("schema_version") != BATCH_SCHEMA_VERSION:
        raise ValueError(f"input must use {BATCH_SCHEMA_VERSION}")
    gate = dict(batch.get("gate") or {})
    minimum_labeled = int(gate.get("minimum_labeled_opportunities", 20))
    minimum_unique = int(gate.get("minimum_unique_labeled_topics", 20))
    k = int(gate.get("k", 5))
    minimum_precision = float(gate.get("minimum_precision_at_k", 0.60))

    queue_results = []
    total_labeled_items = 0
    unique_labeled_topic_ids: set[str] = set()
    complete_precisions: list[float] = []
    relevant_slots = 0
    evaluated_slots = 0
    false_positives = []
    graded_score_sum = 0.0
    graded_evaluated_slots = 0
    graded_possible_slots = 0

    for queue in batch.get("queues") or []:
        document = queue.get("document") or {}
        result = score_labels(document, k=k)
        # 中文说明：20 条门槛统计“人工真正填写了标签的机会数”，不是导出条目数。
        labeled_all = 0
        graded_top_scores: list[float] = []
        graded_possible_slots += min(k, len(document.get("items") or []))
        for item in document.get("items") or []:
            if item.get("relevant") is True or item.get("relevant") is False:
                labeled_all += 1
                # 中文说明：同一 topic 在多个 scope 重复出现时，只算一个去重主题，避免重复样本把 20 条门槛“灌水”。
                topic_key = str(item.get("topic_id") or item.get("topic") or "").strip()
                if topic_key:
                    unique_labeled_topic_ids.add(topic_key)
            rank = int(item.get("rank") or 0)
            graded_score = _graded_relevance_score(item)
            if 1 <= rank <= k and graded_score is not None:
                graded_top_scores.append(graded_score)
                graded_score_sum += graded_score
                graded_evaluated_slots += 1
        total_labeled_items += labeled_all

        precision = result.get("precision_at_k")
        if precision is not None:
            complete_precisions.append(float(precision))
            relevant_slots += int(result.get("relevant_at_k") or 0)
            evaluated_slots += int(result.get("k") or 0)
        for item in result.get("false_positives") or []:
            false_positives.append({
                "scope": document.get("scope"),
                "run_id": document.get("run_id"),
                **item,
            })
        queue_results.append({
            "scope": document.get("scope"),
            "run_id": document.get("run_id"),
            "labeled_items": labeled_all,
            "graded_labeled_at_k": len(graded_top_scores),
            "graded_coverage_at_k": round(len(graded_top_scores) / k, 4) if k else 0.0,
            "mean_relevance_strength_at_k": round(mean(graded_top_scores), 4) if graded_top_scores else None,
            **result,
        })

    macro_precision = round(mean(complete_precisions), 4) if complete_precisions else None
    micro_precision = round(relevant_slots / evaluated_slots, 4) if evaluated_slots else None
    all_top_k_complete = bool(queue_results) and all(
        row.get("label_coverage_at_k") == 1.0 for row in queue_results
    )
    enough_labels = total_labeled_items >= minimum_labeled
    enough_unique = len(unique_labeled_topic_ids) >= minimum_unique
    precision_pass = micro_precision is not None and micro_precision >= minimum_precision
    passed = enough_labels and enough_unique and all_top_k_complete and precision_pass
    graded_mean_relevance = round(graded_score_sum / graded_evaluated_slots, 4) if graded_evaluated_slots else None
    graded_coverage = round(graded_evaluated_slots / graded_possible_slots, 4) if graded_possible_slots else 0.0

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "gate": {
            "minimum_labeled_opportunities": minimum_labeled,
            "minimum_unique_labeled_topics": minimum_unique,
            "k": k,
            "minimum_precision_at_k": minimum_precision,
        },
        "queue_count": len(queue_results),
        "total_labeled_opportunities": total_labeled_items,
        "unique_labeled_topics": len(unique_labeled_topic_ids),
        "all_top_k_complete": all_top_k_complete,
        "macro_precision_at_k": macro_precision,
        "micro_precision_at_k": micro_precision,
        "graded_mean_relevance_at_k": graded_mean_relevance,
        "graded_relevance_coverage_at_k": graded_coverage,
        "graded_labeled_at_k": graded_evaluated_slots,
        "precision_gate_pass": precision_pass,
        "label_count_gate_pass": enough_labels,
        "unique_topic_gate_pass": enough_unique,
        "passed": passed,
        "queues": queue_results,
        "false_positives": false_positives,
    }


def render_markdown(result: Mapping[str, Any]) -> str:
    gate = result.get("gate") or {}
    status = "PASS（通过）" if result.get("passed") else "PENDING / FAIL（待完成 / 未通过）"
    lines = [
        "# Radar R3 Human Validation Report（Radar R3 人工验证报告）",
        "",
        f"- Gate status（门槛状态）: **{status}**",
        f"- Human-labeled opportunities（人工已标注机会）: **{result.get('total_labeled_opportunities', 0)}** / {gate.get('minimum_labeled_opportunities', 20)}",
        f"- Unique labeled topics（去重已标注主题）: **{result.get('unique_labeled_topics', 0)}** / {gate.get('minimum_unique_labeled_topics', 20)}",
        f"- Micro precision@{gate.get('k', 5)}（微平均前五精度）: **{result.get('micro_precision_at_k')}**",
        f"- Macro precision@{gate.get('k', 5)}（宏平均前五精度）: **{result.get('macro_precision_at_k')}**",
        f"- Graded relevance@{gate.get('k', 5)}（分级相关性均值，0~1）: **{result.get('graded_mean_relevance_at_k')}** / coverage（覆盖率） **{result.get('graded_relevance_coverage_at_k')}**",
        f"- Required precision（要求精度）: **>= {gate.get('minimum_precision_at_k', 0.60)}**",
        f"- All Top-K fully labeled（所有前 K 位已完整标注）: **{result.get('all_top_k_complete')}**",
        "",
        "## Queue Results（队列结果）",
        "",
    ]
    for row in result.get("queues") or []:
        lines.append(
            f"- `{row.get('scope')}` / `{row.get('run_id')}`: labels={row.get('labeled_items')}, "
            f"coverage@{row.get('k')}={row.get('label_coverage_at_k')}, precision@{row.get('k')}={row.get('precision_at_k')}"
        )
    lines.extend(["", "## False Positives（误报）", ""])
    if result.get("false_positives"):
        for item in result["false_positives"]:
            lines.append(
                f"- [{item.get('scope')}] rank {item.get('rank')}: `{item.get('topic')}` — {item.get('note') or 'no note（无备注）'}"
            )
    else:
        lines.append("- None recorded（暂无记录）。")
    lines.extend([
        "",
        "## Truth Rule（真相规则）",
        "",
        "只有真实人工标签才计入本报告。模型自评、合成标签和未填写项都不能替代人工 precision gate（精度门槛）。分级相关性是新增诊断指标，不会在本轮 R3 中偷偷替换或放宽已冻结的二元门槛。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar R3 human validation batch")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build an R3 batch from candidate-eval JSON files")
    build.add_argument("inputs", nargs="+")
    build.add_argument("--output", required=True)

    score = sub.add_parser("score", help="Score an R3 batch")
    score.add_argument("input")
    score.add_argument("--output")
    score.add_argument("--markdown")
    score.add_argument("--require-pass", action="store_true")

    args = parser.parse_args()
    if args.command == "build":
        docs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.inputs]
        batch = build_batch(docs, source_paths=args.inputs)
        Path(args.output).write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(args.output)
        return 0

    batch = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = score_batch(batch)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(result), encoding="utf-8")
    return 0 if (result["passed"] or not args.require_pass) else 4


if __name__ == "__main__":
    raise SystemExit(main())
