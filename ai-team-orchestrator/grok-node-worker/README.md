# Grok Node Worker

Grok joins AI Team as a real registered pull worker using the official Grok Build CLI. It reuses the shared Node Worker SDK; the Bridge remains the source of truth for queued/acknowledged/running/completed state.

## Prerequisites

1. Install Grok Build CLI: `curl -fsSL https://x.ai/cli/install.sh | bash`.
2. Authenticate once with `grok login` / `grok login --device-code`, or set `XAI_API_KEY`.
   For unattended AI Team runs, `XAI_API_KEY` or `XAI_API_KEY_FILE` is preferred because the credential source is explicit and reproducible in the worker environment.
   `XAI_API_KEY_FILE` lets a local account manager or secret store expose a read-only file without placing the secret in argv or chat.
3. Start Bridge with `--pull-agent grok` in addition to the other enabled agents.
4. Set `AI_TEAM_BOOTSTRAP_TOKEN_FILE` (or `AI_TEAM_BOOTSTRAP_TOKEN`) for readiness reporting.

## Headless execution

The worker uses Grok's official headless mode with `--no-auto-update`, `--cwd`, `-p`, and `--output-format streaming-json`. Streaming session/tool events are mapped to Orchestrator Progress Events.

Environment options:

- `XAI_API_KEY_FILE`: optional local secret-file path; the worker reads it at runtime and does not print the key
- `GROK_BIN`: Grok executable (default `grok`)
- `GROK_MODEL`: optional model override
- `GROK_REASONING_EFFORT`: optional reasoning effort
- `GROK_PERMISSION_MODE`: default `auto`
- `GROK_ALWAYS_APPROVE=1`: explicitly add `--always-approve`
- `GROK_TASK_TIMEOUT_MS`: bounded task timeout
- `AI_TEAM_BRIDGE_URL`: Bridge URL
- `AI_TEAM_BOOTSTRAP_TOKEN` / `AI_TEAM_BOOTSTRAP_TOKEN_FILE`: readiness auth

Readiness runs a real headless preflight and requires exactly `GROK_MODEL_OK` before the worker registers.

Non-zero CLI exits are classified into machine-readable worker error codes:
`grok_quota_exhausted`, `grok_rate_limited`, `grok_auth_required`, or
`grok_runtime_error`. Supervisor/fallback logic can use these codes instead
of parsing human-readable stderr.
