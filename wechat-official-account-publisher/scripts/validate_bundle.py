#!/usr/bin/env python3
"""Validate a WeChat article bundle without external dependencies."""
import argparse, json, sys
from pathlib import Path

REQUIRED_FILES = ("research_pack.json", "article.json", "review.json", "content.html")
CHECKS = ("facts", "citations", "copyright", "privacy", "safety", "disclosure", "formatting")

def load_json(path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ValueError(f"Cannot read valid JSON from {path.name}: {exc}") from exc

def validate(directory, require_media=False):
    errors = [f"missing {n}" for n in REQUIRED_FILES if not (directory / n).is_file()]
    if errors: return errors
    research, article, review = (load_json(directory / n) for n in ("research_pack.json", "article.json", "review.json"))
    html = (directory / "content.html").read_text(encoding="utf-8")
    for field in ("topic", "as_of", "audience", "objective", "sources", "claims"):
        if field not in research: errors.append(f"research_pack.json missing {field}")
    source_ids = {s.get("id") for s in research.get("sources", []) if isinstance(s, dict)}
    for claim in research.get("claims", []):
        if not claim.get("source_ids") and claim.get("status") != "unresolved": errors.append(f"claim {claim.get('id', '?')} has no sources")
        for sid in claim.get("source_ids", []):
            if sid not in source_ids: errors.append(f"claim {claim.get('id', '?')} references unknown source {sid}")
    for field in ("title", "digest", "content_html_file"):
        if not article.get(field): errors.append(f"article.json missing {field}")
    if article.get("ai_generated") and not article.get("ai_disclosure"): errors.append("AI-generated article requires ai_disclosure")
    if require_media and not article.get("cover_media_id"): errors.append("cover_media_id is required for submission")
    if article.get("content_html_file") != "content.html": errors.append("content_html_file must be content.html")
    if review.get("status") != "approved": errors.append("review status must be approved")
    for name in CHECKS:
        if review.get("checks", {}).get(name) not in ("pass", "warn"): errors.append(f"review check {name} must be pass or warn")
    lowered = html.lower()
    for forbidden in ("<script", "<iframe", "<form", "javascript:"):
        if forbidden in lowered: errors.append(f"content.html contains forbidden markup: {forbidden}")
    if not html.strip(): errors.append("content.html is empty")
    return errors

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("bundle_dir", type=Path); parser.add_argument("--require-media", action="store_true"); args = parser.parse_args()
    try: errors = validate(args.bundle_dir, args.require_media)
    except ValueError as exc: errors = [str(exc)]
    if errors:
        for error in errors: print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Bundle validation passed"); return 0

if __name__ == "__main__": raise SystemExit(main())
