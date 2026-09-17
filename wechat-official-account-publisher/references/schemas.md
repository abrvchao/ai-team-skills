# Bundle schemas and acceptance criteria

All JSON files are UTF-8. Unknown fields are allowed for forward compatibility.

## `research_pack.json`

Required fields:

```json
{
  "topic": "string",
  "as_of": "YYYY-MM-DD",
  "audience": "string",
  "objective": "string",
  "sources": [{"id": "S1", "title": "string", "url": "https://...", "publisher": "string", "published_at": "date or null", "accessed_at": "date", "source_type": "primary|secondary|user-provided", "credibility": "high|medium|low"}],
  "claims": [{"id": "C1", "claim": "string", "source_ids": ["S1"], "confidence": "high|medium|low", "status": "verified|qualified|unresolved"}],
  "insights": ["string"],
  "open_questions": ["string"]
}
```

Every factual claim intended for the article needs at least one source. Unresolved claims must not be stated as fact.

## `article.json`

```json
{"title":"string","subtitle":"string or empty","digest":"string","author":"string","content_html_file":"content.html","cover_media_id":"string or empty until upload","source_ids":["S1"],"ai_generated":true,"ai_disclosure":"string","cta":"string","risk_flags":[]}
```

`title`, `digest`, and `content_html_file` are mandatory. `ai_disclosure` must be non-empty when `ai_generated` is true.

## `review.json`

```json
{"status":"approved|needs_changes|blocked","checked_at":"ISO-8601 timestamp","checks":{"facts":"pass|warn|fail","citations":"pass|warn|fail","copyright":"pass|warn|fail","privacy":"pass|warn|fail","safety":"pass|warn|fail","disclosure":"pass|warn|fail","formatting":"pass|warn|fail"},"issues":[{"severity":"low|medium|high","message":"string","action":"string"}],"reviewer_summary":"string"}
```

Submission requires `status: approved` and no failed checks.

## `content.html`

Use semantic headings and paragraphs with conservative inline CSS. Images must use WeChat-hosted URLs before submission. Do not include JavaScript, forms, iframes, tracking pixels, or remote CSS.
