---
name: wechat-official-account-publisher
description: Research, draft, fact-check, review, format, and optionally submit articles to a WeChat Official Account draft box. Use for 公众号选题分析、文章创作、排版、草稿包生成或经确认后写入草稿箱；不用于自动公开发布。
---

# WeChat Official Account Publisher

Produce a reviewable article package from a topic or source set. Default to **Draft mode**: stop at a validated local draft bundle unless the user explicitly authorizes a draft-box API write in the current task.

## Modes

- `manual`: research, write, review, and return the article package.
- `draft` (default): also format WeChat-compatible HTML and validate the bundle.
- `submit-draft`: upload assets and create a WeChat draft only after explicit confirmation, credentials are configured, and validation passes.

Never publish publicly. A request to publish is outside this skill; stop after creating the draft and ask the user to review it in WeChat.

## Workflow

1. Establish topic, audience, objective, voice, constraints, and provided sources. Infer ordinary editorial choices; ask only when a missing choice materially changes the result.
2. Build a Research Pack before drafting factual claims. Separate sourced facts, interpretations, and unresolved claims. For current or consequential facts, use primary sources where possible.
3. Choose an angle and outline. Avoid duplicating source structure or close paraphrase.
4. Draft title candidates, digest, body, CTA, and source notes. Facts in the article must trace to the Research Pack; clearly label opinion or inference.
5. Review for factual support, copyright risk, sensitive claims, privacy, promotional overstatement, unsupported medical/legal/financial advice, and AI-generated-content disclosure needs.
6. Produce WeChat-safe HTML using simple inline styles. Do not include scripts, forms, external trackers, or unsupported interactive elements.
7. Create `research_pack.json`, `article.json`, `content.html`, and `review.json` according to [references/schemas.md](references/schemas.md), then run `scripts/validate_bundle.py`.
8. If `submit-draft` is authorized, read [references/wechat-api.md](references/wechat-api.md), use `scripts/wechat_adapter.py`, and return the API result plus a redacted operation log. Never print secrets or access tokens.

## Required gates

- Block submission if the review status is `blocked`, required citations are missing, the cover asset has no `media_id`, or `WECHAT_APP_ID` / `WECHAT_APP_SECRET` are absent.
- Treat external API submission as a mutation. Confirm the target account and obtain current-task authorization immediately before the call.
- Never silently retry a draft creation request after an ambiguous network failure; first determine whether a draft was created.
- Do not claim that content reached the draft box unless the API returned a successful media identifier.
- Do not upload sources, private notes, or credentials as article content.

## Supervisor handoff

Return a compact status object containing: `mode`, `topic`, `angle`, `artifacts`, `review_status`, `risk_flags`, `submission_status`, `draft_media_id`, and `next_action`. Escalate unresolved factual disputes or high-impact compliance issues to a human reviewer.

For the exact schemas and acceptance criteria, read [references/schemas.md](references/schemas.md). For disclosure and review rules, read [references/review-policy.md](references/review-policy.md).
