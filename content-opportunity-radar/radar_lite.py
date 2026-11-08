"""Radar Lite（精简版）: discover → rank → evidence → handoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_team_handoff import build_handoff, render_markdown as render_handoff_markdown
from radar import run_discovery
from read_model import OpportunityReadStore


LITE_SCHEMA_VERSION = "radar-lite-v1"
DEFAULT_DB = ".radar/radar.db"


def build_cards(
    store: OpportunityReadStore,
    *,
    scope: str,
    limit: int = 5,
    workspace_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """把完整 Radar 结果压缩成用户真正需要的机会卡。"""
    page = store.list_opportunities(
        scope=scope,
        limit=max(1, min(int(limit), 10)),
        workspace_id=workspace_id,
        run_id=run_id,
    )
    run = page.get("run")
    cards: list[dict[str, Any]] = []
    for item in page.get("items") or []:
        pack = store.research_pack(
            item["topic_id"],
            scope=scope,
            workspace_id=workspace_id,
        ) or {}
        citations = list(pack.get("citations") or [])
        coverage = dict(pack.get("coverage") or {})
        providers = [
            str(row.get("provider"))
            for row in (item.get("source_summary") or [])
            if row.get("provider")
        ]
        cards.append(
            {
                "rank": item.get("rank"),
                "topic_id": item.get("topic_id"),
                "topic": item.get("topic"),
                "opportunity_score": (item.get("scores") or {}).get("opportunity"),
                "confidence": item.get("confidence"),
                "freshness": item.get("freshness"),
                "reasons": list(item.get("reasons") or [])[:3],
                "providers": providers,
                "citation_count": len(citations),
                "missing_dimensions": list(coverage.get("missing_dimensions") or []),
                "handoff_ready": bool(citations),
            }
        )
    resolved_run_id = (
        run.get("run_id")
        if isinstance(run, dict)
        else getattr(run, "run_id", None)
        if run
        else None
    )
    return {
        "schema_version": LITE_SCHEMA_VERSION,
        "mode": "lite",
        "scope": scope,
        "run_id": resolved_run_id,
        "cards": cards,
        "next_action": (
            "选择一个 handoff_ready（可交接）机会交给 AI Team；"
            "Lite 模式下不扩 Provider（数据源）或 scoring（评分）。"
        ),
    }


def render_cards_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Radar Lite — {payload.get('scope')}",
        "",
        f"- Run（运行）: {payload.get('run_id')}",
        f"- Opportunities（机会数）: {len(payload.get('cards') or [])}",
        "",
    ]
    for card in payload.get("cards") or []:
        lines.extend(
            [
                f"## #{card.get('rank')} {card.get('topic')}",
                "",
                f"- Opportunity score（机会分）: {card.get('opportunity_score')}",
                f"- Confidence（置信度）: {card.get('confidence')}",
                f"- Evidence（证据）: {card.get('citation_count')} citations / {len(card.get('providers') or [])} providers",
                f"- Handoff ready（可交接）: {card.get('handoff_ready')}",
            ]
        )
        for reason in card.get("reasons") or []:
            lines.append(f"- Why（原因）: {reason}")
        gaps = card.get("missing_dimensions") or []
        if gaps:
            lines.append(f"- Gaps（缺口）: {', '.join(gaps)}")
        lines.append("")
    lines.extend(["## Next（下一步）", "", str(payload.get("next_action") or ""), ""])
    return "\n".join(lines)


def handoff_by_rank(
    store: OpportunityReadStore,
    *,
    scope: str,
    rank: int,
    workspace_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    page = store.list_opportunities(
        scope=scope,
        limit=max(1, int(rank)),
        workspace_id=workspace_id,
        run_id=run_id,
    )
    target = next(
        (
            item
            for item in (page.get("items") or [])
            if int(item.get("rank") or 0) == int(rank)
        ),
        None,
    )
    if not target:
        raise ValueError(f"rank {rank} not found for scope {scope}")
    pack = store.research_pack(
        target["topic_id"],
        scope=scope,
        workspace_id=workspace_id,
    )
    if not pack:
        raise ValueError(f"research pack missing for {target['topic_id']}")
    return build_handoff(target, pack)


def run_lite_scan(
    *,
    scope: str,
    db: str = DEFAULT_DB,
    top: int = 5,
    language: str = "en",
    country: str = "US",
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """运行精简发现链；显式关闭实验扩源、GSC、广告和网页深抓取。"""
    run_discovery(
        scope=scope,
        seed_limit=12,
        candidate_count=max(5, int(top)),
        top_n=max(1, min(int(top), 5)),
        deep_limit=5,
        read_model_db=db,
        workspace_id=workspace_id,
        language=language,
        country=country,
        include_research_pack=True,
        seed_mode="native",
        website=None,
        gsc_site_url=None,
        gsc_access_token=None,
        google_ads_customer_id=None,
        google_ads_access_token=None,
        hydrate_content=False,
    )
    with OpportunityReadStore(db, read_only=True, initialize=False) as store:
        return build_cards(
            store,
            scope=scope,
            limit=top,
            workspace_id=workspace_id,
        )


def _write_text(text: str, output: str | None) -> None:
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    print(text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Radar Lite（精简版）：发现 → 排序 → 证据 → AI Team 交接"
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="运行精简发现链")
    scan.add_argument("--scope", default="AI Agents")
    scan.add_argument("--top", type=int, default=5)
    scan.add_argument("--language", default="en")
    scan.add_argument("--country", default="US")
    scan.add_argument("--workspace-id")
    scan.add_argument("--output")

    cards = sub.add_parser("cards", help="从现有结果生成机会卡，不触发外部采集")
    cards.add_argument("--scope", default="AI Agents")
    cards.add_argument("--top", type=int, default=5)
    cards.add_argument("--workspace-id")
    cards.add_argument("--run-id")
    cards.add_argument("--format", choices=("markdown", "json"), default="markdown")
    cards.add_argument("--output")

    handoff = sub.add_parser("handoff", help="把指定排名机会打包给 AI Team")
    handoff.add_argument("--scope", default="AI Agents")
    handoff.add_argument("--rank", type=int, required=True)
    handoff.add_argument("--workspace-id")
    handoff.add_argument("--run-id")
    handoff.add_argument("--output", required=True)
    handoff.add_argument("--markdown-output")

    args = parser.parse_args()

    if args.command == "scan":
        payload = run_lite_scan(
            scope=args.scope,
            db=args.db,
            top=args.top,
            language=args.language,
            country=args.country,
            workspace_id=args.workspace_id,
        )
        _write_text(render_cards_markdown(payload), args.output)
        return 0

    with OpportunityReadStore(args.db, read_only=True, initialize=False) as store:
        if args.command == "cards":
            payload = build_cards(
                store,
                scope=args.scope,
                limit=args.top,
                workspace_id=args.workspace_id,
                run_id=args.run_id,
            )
            text = (
                json.dumps(payload, ensure_ascii=False, indent=2)
                if args.format == "json"
                else render_cards_markdown(payload)
            )
            _write_text(text, args.output)
            return 0

        payload = handoff_by_rank(
            store,
            scope=args.scope,
            rank=args.rank,
            workspace_id=args.workspace_id,
            run_id=args.run_id,
        )
        _write_text(json.dumps(payload, ensure_ascii=False, indent=2), args.output)
        if args.markdown_output:
            _write_text(render_handoff_markdown(payload), args.markdown_output)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
