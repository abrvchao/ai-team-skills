"""Human-label evaluation harness for Radar candidate/relevance quality."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from read_model import OpportunityReadStore


SCHEMA_VERSION = "candidate-eval-v1"


def evidence_providers(row: Mapping[str, Any]) -> list[str]:
    return sorted({
        str(item.get("provider"))
        for item in (row.get("source_summary") or [])
        if isinstance(item, Mapping) and item.get("provider")
    })


def export_queue(
    *,
    database: str,
    scope: str,
    limit: int = 20,
    workspace_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    with OpportunityReadStore(database, initialize=False, read_only=True) as store:
        page = store.list_opportunities(
            scope=scope,
            limit=limit,
            workspace_id=workspace_id,
            run_id=run_id,
        )
    run = page.get("run")
    if not run:
        raise ValueError(f"no Radar run found for scope={scope!r}")

    items = []
    for row in page.get("items") or []:
        discovery = row.get("discovery") or {}
        seed_providers = discovery.get("providers") or []
        items.append(
            {
                "rank": row["rank"],
                "topic_id": row["topic_id"],
                "topic": row["topic"],
                "scores": row.get("scores") or {},
                "confidence": row.get("confidence"),
                "providers": seed_providers,
                "seed_providers": seed_providers,
                "evidence_providers": evidence_providers(row),
                "aliases": discovery.get("aliases") or [],
                "evidence_count": len(row.get("evidence_ids") or []),
                "relevant": None,
                "note": "",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "workspace_id": workspace_id,
        "run_id": run["run_id"],
        "seed_source_mode": run.get("seed_source_mode"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labeling_instructions": {
            "relevant": (
                "Set true only if this is a specific, actionable content opportunity "
                "for the stated scope. Set false for ambiguity, generic categories, "
                "off-scope topics, or evidence polluted by another meaning."
            ),
            "note": "Briefly explain false labels so discovery rules can be improved deterministically.",
        },
        "items": items,
    }


def _label(value: Any) -> bool | None:
    if value is True or value is False:
        return value
    if value is None:
        return None
    text = str(value).strip().casefold()
    if text in {"true", "yes", "relevant", "1"}:
        return True
    if text in {"false", "no", "irrelevant", "0"}:
        return False
    return None


def score_labels(document: Mapping[str, Any], *, k: int = 5) -> dict[str, Any]:
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"input must use {SCHEMA_VERSION}")
    items = list(document.get("items") or [])
    if not items:
        raise ValueError("evaluation input has no items")
    k = max(1, min(int(k), len(items)))
    top = items[:k]
    labeled = [(item, _label(item.get("relevant"))) for item in top]
    labeled = [(item, label) for item, label in labeled if label is not None]
    relevant = [item for item, label in labeled if label is True]
    irrelevant = [item for item, label in labeled if label is False]
    coverage = len(labeled) / k
    labeled_precision = len(relevant) / len(labeled) if labeled else None
    precision_at_k = len(relevant) / k if len(labeled) == k else None
    return {
        "schema_version": "candidate-eval-result-v1",
        "scope": document.get("scope"),
        "run_id": document.get("run_id"),
        "k": k,
        "labeled_at_k": len(labeled),
        "label_coverage_at_k": round(coverage, 4),
        "relevant_at_k": len(relevant),
        "irrelevant_at_k": len(irrelevant),
        "labeled_precision_at_k": (
            round(labeled_precision, 4) if labeled_precision is not None else None
        ),
        "precision_at_k": (
            round(precision_at_k, 4) if precision_at_k is not None else None
        ),
        "false_positives": [
            {
                "rank": item.get("rank"),
                "topic_id": item.get("topic_id"),
                "topic": item.get("topic"),
                "note": item.get("note") or "",
            }
            for item in irrelevant
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Radar candidate relevance")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Export latest Radar ranking for human labeling")
    export.add_argument("--db", default=".radar/radar.db")
    export.add_argument("--scope", required=True)
    export.add_argument("--workspace-id")
    export.add_argument("--run-id", help="Export one historical Radar run instead of only the latest run")
    export.add_argument("--limit", type=int, default=20)
    export.add_argument("--output", required=True)

    score = sub.add_parser("score", help="Score a human-labeled evaluation file")
    score.add_argument("input")
    score.add_argument("--k", type=int, default=5)
    score.add_argument("--require-complete-top-k", action="store_true")
    score.add_argument("--min-precision", type=float)

    args = parser.parse_args()
    if args.command == "export":
        result = export_queue(
            database=args.db,
            scope=args.scope,
            workspace_id=args.workspace_id,
            run_id=args.run_id,
            limit=args.limit,
        )
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(args.output)
        return 0

    document = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = score_labels(document, k=args.k)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_complete_top_k and result["label_coverage_at_k"] < 1.0:
        return 2
    if args.min_precision is not None:
        precision = result["precision_at_k"]
        if precision is None or precision < float(args.min_precision):
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
