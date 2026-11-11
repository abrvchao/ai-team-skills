"""Build an offline R3 human-label pack（离线生成 R3 人工标注包） from real stored events."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector import RadarStore
from discovery import discover_candidates
from r3_validation import build_batch, render_markdown, score_batch

# 中文说明：R3 的范围池在任何人工标签产生前固定；选择只看“是否有完整 Top-5”
# 和“是否累计到足够去重主题”，绝不根据人工 precision 结果事后挑范围。
R3_SCOPE_POOL = [
    "AI Coding",
    "AI Agents",
    "AI Engineering",
    "AI Memory",
    "AI Tools",
    "AI Safety",
    "AI Search",
    "LLM Evaluation",
    "AI Infrastructure",
]
MINIMUM_UNIQUE_TOPICS = 20


def _candidate_document(scope: str, candidates: list) -> dict:
    items = []
    for rank, row in enumerate(candidates, 1):
        items.append({
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
            # 中文说明：模型和代码只能生成待标注槽位；真人标签必须保持空值。
            "relevant": None,
            "relevance_level": None,
            "relevance_score": None,
            "note": "",
        })
    return {
        "schema_version": "candidate-eval-v1",
        "scope": scope,
        "workspace_id": None,
        "run_id": f"offline-real-events:{scope}",
        "seed_source_mode": "stored-real-events",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labeling_instructions": {
            "relevance_level": "人工判断相关性强弱：strong / medium / weak / unrelated；若确实无法判断才填 uncertain。",
            "relevant": "兼容旧二元验收：strong/medium -> true，unrelated -> false，weak/uncertain -> null。",
            "note": "弱相关、不相关或无法判断时，建议简短说明原因。",
        },
        "items": items,
    }


def _select_validation_documents(events, *, scopes: list[str], k: int, minimum_unique_topics: int) -> tuple[list[dict], dict]:
    documents: list[dict] = []
    unique_topics: set[str] = set()
    skipped: list[dict] = []
    selected: list[str] = []

    for scope in scopes:
        candidates = discover_candidates(events, scope=scope, max_candidates=max(k, 20))[:k]
        if len(candidates) < k:
            # 中文说明：Top-5 不完整的范围不能进入 precision@5 人工验证，避免先天无法完成的队列。
            skipped.append({"scope": scope, "reason": "underfilled_top_k", "candidate_count": len(candidates)})
            continue
        documents.append(_candidate_document(scope, candidates))
        selected.append(scope)
        unique_topics.update(row.id for row in candidates if row.id)
        if len(unique_topics) >= minimum_unique_topics:
            break

    if len(unique_topics) < minimum_unique_topics:
        raise ValueError(
            f"R3 evidence is insufficient before labeling: {len(unique_topics)} unique topics "
            f"< required {minimum_unique_topics}"
        )
    return documents, {
        "method": "pre-label-evidence-sufficiency-v1",
        "label_dependent": False,
        "scope_pool": list(scopes),
        "selected_scopes": selected,
        "skipped_scopes": skipped,
        "minimum_unique_topics": minimum_unique_topics,
        "selected_unique_topics": len(unique_topics),
    }


def build_label_pack(
    *,
    database: str,
    scopes: list[str],
    k: int = 5,
    since_hours: int = 168,
    minimum_unique_topics: int = MINIMUM_UNIQUE_TOPICS,
) -> dict:
    with RadarStore(database) as store:
        events = store.recent_events(
            since=datetime.now(timezone.utc) - timedelta(hours=since_hours),
            limit=5000,
        )
    documents, selection = _select_validation_documents(
        events,
        scopes=scopes,
        k=k,
        minimum_unique_topics=minimum_unique_topics,
    )
    batch = build_batch(documents)
    batch["selection"] = selection
    return batch


def render_labeling_sheet(batch: dict) -> str:
    lines = [
        "# Radar R3 Human Label Sheet（Radar R3 人工标注表）",
        "",
        "按真实产品用户感觉判断相关性强弱：强相关 / 中相关 / 弱相关 / 不相关；只有确实无法判断时才选“无法判断”。不要把“弱相关”和“不确定”混为一谈。",
        "冻结的旧 binary precision（二元精度）仍保留：强/中相关 -> `relevant=true`；不相关 -> `relevant=false`；弱相关/无法判断 -> `relevant=null`。新增 graded relevance（分级相关性）只做诊断，不中途改门槛。",
        "**不要让 AI 自动代填这些标签**，否则不能算 R3 人工验证。",
        "",
    ]
    unique = set()
    total = 0
    for queue in batch.get("queues") or []:
        doc = queue.get("document") or {}
        lines.extend([f"## {doc.get('scope')}", ""])
        for item in doc.get("items") or []:
            total += 1
            unique.add(item.get("topic_id"))
            level = item.get("relevance_level")
            relevant = item.get("relevant")
            note = str(item.get("note") or "")
            checked = "x" if (level is not None or relevant is True or relevant is False) else " "
            lines.append(
                f"- [{checked}] Rank {item.get('rank')} — **{item.get('topic')}** — "
                f"relevance_level: `{level if level is not None else 'null'}` — "
                f"relevant: `{json.dumps(relevant)}` — note: `{note}`"
            )
        lines.append("")
    lines.extend([
        "## Readiness（准备度）",
        "",
        f"- Label slots（待标注位置）: **{total}**",
        f"- Unique topics（去重主题）: **{len(unique)}**",
        "- R3 gate（R3 门槛）: 至少 20 个去重主题被人工标注，所有参与队列 Top-5 完整标注，micro precision@5 >= 0.60。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Radar R3 label pack from stored real events")
    parser.add_argument("--db", default=".radar/radar.db")
    parser.add_argument("--scope", action="append", dest="scopes")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--since-hours", type=int, default=168)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--result")
    parser.add_argument("--report")
    args = parser.parse_args()
    batch = build_label_pack(
        database=args.db,
        scopes=args.scopes or R3_SCOPE_POOL,
        k=args.k,
        since_hours=args.since_hours,
    )
    Path(args.output).write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.sheet:
        Path(args.sheet).write_text(render_labeling_sheet(batch), encoding="utf-8")
    result = score_batch(batch)
    if args.result:
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report:
        Path(args.report).write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({
        "output": args.output,
        "queue_count": len(batch.get("queues") or []),
        "label_slots": sum(len((q.get("document") or {}).get("items") or []) for q in batch.get("queues") or []),
        "unique_topics": len({i.get("topic_id") for q in batch.get("queues") or [] for i in (q.get("document") or {}).get("items") or [] if i.get("topic_id")}),
        "gate_passed": result["passed"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
