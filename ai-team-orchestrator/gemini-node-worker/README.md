# Gemini Node Worker

Gemini joins AI Team as a real registered pull worker. It reuses the same
`AgentWorker` contract as DSH; the Bridge remains the source of truth for
queued/acknowledged/running/completed state.

## Prerequisites

1. Install the official Gemini CLI.
2. Authenticate Gemini CLI once on the local machine.
3. Start the Bridge with Gemini explicitly enabled:

```bash
python bridge.py \
  --workspace-root /path/to/workspaces \
  --pull-agent dsh \
  --pull-agent gemini
```

4. Point the worker at the Bridge bootstrap token:

```bash
export AI_TEAM_BOOTSTRAP_TOKEN_FILE=/path/to/.orchestrator/bootstrap-token
export AI_TEAM_BRIDGE_URL=http://127.0.0.1:8765
npm start
```

The worker performs a real model preflight before registration. If preflight
fails, Gemini is not registered and therefore cannot become `available=true`.

## Gemini CLI execution

The worker uses official headless mode:

```bash
gemini --prompt "..." --output-format stream-json --approval-mode auto_edit
```

Environment options:

- `GEMINI_BIN`: executable path (default `gemini`)
- `GEMINI_MODEL`: optional model
- `GEMINI_APPROVAL_MODE`: default `auto_edit`; `yolo` is never enabled by default
- `GEMINI_SANDBOX=1`: enable Gemini sandbox
- `GEMINI_SKIP_TRUST=1`: explicitly bypass folder trust
- `GEMINI_TASK_TIMEOUT_MS`: task timeout

When the shared Node Worker SDK supports progress, Gemini maps stream-json
events into structured progress stages. Artifact paths remain workspace-relative
and are validated by the Bridge.
