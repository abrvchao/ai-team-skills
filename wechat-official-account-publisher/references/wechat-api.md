# WeChat draft API adapter

Read this only for `submit-draft` mode.

The adapter uses configurable endpoints because official WeChat documentation may be inaccessible from some environments and API details can change. Before production use, verify account eligibility, payload fields, image/material rules, and endpoint paths against the current official documentation at `developers.weixin.qq.com`.

## Environment

- `WECHAT_APP_ID`
- `WECHAT_APP_SECRET`
- `WECHAT_TOKEN_URL` (default: `https://api.weixin.qq.com/cgi-bin/token`)
- `WECHAT_DRAFT_ADD_URL` (default: `https://api.weixin.qq.com/cgi-bin/draft/add`)
- `WECHAT_TIMEOUT_SECONDS` (default: `20`)

Never store secrets inside the bundle or commit them. Obtain an access token only at execution time and redact it from logs.

## Preconditions

1. `scripts/validate_bundle.py BUNDLE_DIR` succeeds.
2. `review.json` is approved.
3. `article.json.cover_media_id` is populated by an authorized material-upload step.
4. The user confirms the target account and draft creation in the current task.

Dry run: `python3 scripts/wechat_adapter.py BUNDLE_DIR --dry-run`

Authorized mutation: `python3 scripts/wechat_adapter.py BUNDLE_DIR --submit`

The script never publishes, never logs secrets, and does not retry draft creation automatically.
