# Muse Node Worker

Muse joins AI Team as a registered pull worker backed by Meta's official Muse
Code CLI.

Runtime defaults:
- CLI: `muse`
- model: `muse-spark-1.3`
- reasoning effort: `high`
- headless mode: `muse exec --json`
- approvals: disabled for unattended execution while the Muse OS sandbox stays on
- maximum model steps: `80`

The worker uses Muse Code's own stored browser/API credential. It does not copy
credentials from the Muse desktop app. Authenticate the CLI once with:

```bash
muse login
```

For non-interactive infrastructure, Meta also supports `META_API_KEY` or
`muse auth set`.

Environment overrides:
- `MUSE_BIN`
- `MUSE_MODEL`
- `MUSE_REASONING_EFFORT`
- `MUSE_MAX_MODEL_STEPS`
- `MUSE_TASK_TIMEOUT_MS`
- `MUSE_DISABLE_WEB_TOOLS=1`

The worker performs a real `MUSE_MODEL_OK` model preflight before registering.
It maps JSONL session/tool events to Orchestrator Progress Events and validates
every required artifact before reporting task completion.
