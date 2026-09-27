# DSH Node Worker

This adapter runs the installed DSH headless Node entrypoint as the real task
handler behind the AI Team Orchestrator Node Worker SDK.

The DSH entrypoint is configurable with `DSH_BIN`; `DSH_NODE_BIN` can select the
Node runtime and `DSH_HOME` selects the DSH profile home. The default values
match the local DSH installation used for validation.

```bash
AI_TEAM_BRIDGE_URL=http://127.0.0.1:8765 \
DSH_HOME="$HOME/.dsh" \
node src/index.js
```

## Readiness gate

At process startup the adapter runs a minimal model preflight before calling
`AgentWorker.start()`. A successful preflight is followed by worker registration
and heartbeat. If the model is unavailable, the DSH process remains alive and
reports `available: false, reason: "model_unavailable"` to the Bridge; it does
not lease formal tasks and retries readiness periodically.

`handleTask` passes task title, prompt, workspace, cancellation, and follow-up
messages to the real DSH headless runner. It reports the task outputs required by
the smoke and CN-DSH-001 tasks as artifacts.
