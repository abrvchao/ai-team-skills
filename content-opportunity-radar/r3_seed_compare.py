"""R3 native vs experimental seed comparison（R3 原生与实验种子对照）."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from candidate_eval import SCHEMA_VERSION, score_labels
from core import RawEvent
from discovery import discover_candidates


EXPERIMENT_SCHEMA = "r3-experimental-candidate-eval-v1"
COMPARE_SCHEMA = "r3-seed-compare-result-v1"


def build_experimental_document(*, fixture_path: str | Path, k: int = 5) -> dict[str, Any]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    scope = str(fixture.get("scope") or "AI Agents")
    events = [RawEvent.from_dict(row) for row in fixture.get("events") or []]
    rows = discover_candidates(events, scope=scope, max_candidates=max(20, k))[:k]
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_schema": EXPERIMENT_SCHEMA,
        "scope": scope,
        "workspace_id": None,
        "run_id": "experimental-aihot-fixture",
        "seed_source_mode": "experimental_aihot_real_observations_fixture",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance_note": fixture.get("provenance_note"),
        "promotion_allowed": False,
        "labeling_instructions": {
            "relevant": "必须由真人按同一 scope 标准填 true/false；AI 不得代填。",
            "note": "若不相关，记录噪声/过泛/越界原因。",
        },
        "items": [
            {
                "rank": rank,
                "topic_id": row.id,
                "topic": row.name,
                "scores": {"discovery": row.discovery_score},
                "confidence": None,
                "providers": row.providers,
                "seed_providers": row.providers,
                "evidence_providers": row.providers,
                "aliases": row.aliases,
                "evidence_count": len(row.evidence_ids),
                "relevant": None,
                "note": "",
            }
            for rank, row in enumerate(rows, 1)
        ],
    }


def _native_doc(batch: Mapping[str, Any], scope: str) -> dict[str, Any]:
    for queue in batch.get("queues") or []:
        doc = queue.get("document") or {}
        if doc.get("scope") == scope:
            return dict(doc)
    raise ValueError(f"native batch has no scope: {scope}")


def score_compare(*, native_batch: Mapping[str, Any], experimental: Mapping[str, Any], k: int = 5) -> dict[str, Any]:
    scope = str(experimental.get("scope") or "")
    native = _native_doc(native_batch, scope)
    native_score = score_labels(native, k=k)
    experimental_score = score_labels(experimental, k=k)
    n = native_score.get("precision_at_k")
    e = experimental_score.get("precision_at_k")
    complete = n is not None and e is not None
    delta = round(float(e) - float(n), 4) if complete else None
    if not complete:
        comparison = "pending_human_labels"
    elif delta > 0:
        comparison = "experimental_higher_precision"
    elif delta < 0:
        comparison = "native_higher_precision"
    else:
        comparison = "tie"
    # 中文说明：precision 更高也不等于自动晋级生产数据源；授权、稳定性、条款和来源完整性仍是独立门槛。
    return {
        "schema_version": COMPARE_SCHEMA,
        "scope": scope,
        "k": k,
        "complete": complete,
        "native": native_score,
        "experimental": experimental_score,
        "precision_delta_experimental_minus_native": delta,
        "comparison": comparison,
        "automatic_provider_promotion": False,
        "promotion_note": "Human relevance is one input only; production promotion still requires authorization, reliability and provenance gates（人工相关性只是输入之一，生产晋级仍需授权、稳定性和来源门槛）.",
    }


def render_sheet(doc: Mapping[str, Any]) -> str:
    lines = [
        "# R3 AIHOT Experimental Label Sheet（R3 AIHOT 实验人工标注表）",
        "",
        f"- Scope（范围）: `{doc.get('scope')}`",
        f"- Provenance（来源）: {doc.get('provenance_note')}",
        "- 规则：与 native（原生）队列使用完全相同的人类判断标准；AI 不得代填。",
        "- `promotion_allowed=false`：标注结果不会自动把 AIHOT 晋级为生产 provider（数据提供器）。",
        "",
    ]
    for item in doc.get("items") or []:
        lines.append(
            f"- [ ] Rank {item.get('rank')} — **{item.get('topic')}** — relevant: `null` — note: ``"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare native and experimental R3 candidate relevance")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build-experimental")
    b.add_argument("--fixture", default="fixtures/aihot_chinese_v1.json")
    b.add_argument("--output", required=True)
    b.add_argument("--sheet")
    s = sub.add_parser("score")
    s.add_argument("--native-batch", required=True)
    s.add_argument("--experimental", required=True)
    s.add_argument("--output")
    args = parser.parse_args()
    if args.command == "build-experimental":
        doc = build_experimental_document(fixture_path=args.fixture)
        Path(args.output).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.sheet:
            Path(args.sheet).write_text(render_sheet(doc), encoding="utf-8")
        print(json.dumps({"items": len(doc["items"]), "scope": doc["scope"], "promotion_allowed": doc["promotion_allowed"]}, ensure_ascii=False))
        return 0
    native = json.loads(Path(args.native_batch).read_text(encoding="utf-8"))
    experimental = json.loads(Path(args.experimental).read_text(encoding="utf-8"))
    result = score_compare(native_batch=native, experimental=experimental)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
