# DSH Node Worker

This adapter runs the installed DSH headless Node entrypoint as the real task
handler behind the AI Team Orchestrator Node Worker SDK.

## Configuration

No machine-specific path is baked in. Every value is configured or derived:

| Variable | Meaning | Default |
| --- | --- | --- |
| `DSH_BIN` | Absolute path to the installed DSH `lib/bin.js`. | Discovered at `$DSH_HOME/profiles/node_modules/@deepseek-ai/dsh/lib/bin.js`. Startup fails with a clear error if neither exists. |
| `DSH_HOME` | DSH profile home. | `$HOME/.dsh` |
| `DSH_NODE_BIN` | Node runtime used to launch DSH. | The current `process.execPath` |
| `DSH_PROFILE` | DSH profile to boot. | `headless` |
| `AI_TEAM_BRIDGE_URL` | Local Bridge base URL. | `http://127.0.0.1:8765` |
| `AI_TEAM_BOOTSTRAP_TOKEN` | Bridge readiness/bootstrap token (preferred). **Required.** | unset |
| `AI_TEAM_BOOTSTRAP_TOKEN_FILE` | File holding the bootstrap token, as printed by the Bridge at startup. **Required** when the token variable is not set. | unset |
| `DSH_TASK_TIMEOUT_MS` | Bounded timeout for one real task run. | `1800000` |

```bash
AI_TEAM_BRIDGE_URL=http://127.0.0.1:8765 \
AI_TEAM_BOOTSTRAP_TOKEN_FILE=./.orchestrator/bootstrap-token \
node src/index.js
```

The bootstrap token is **required** for this adapter, because readiness
reporting is authenticated. When neither variable is set, the adapter fails fast
with `bootstrap_token_required` before running any preflight or entering the
readiness loop, instead of retrying readiness reports that the Bridge would
reject with `403`.

The Bridge writes its bootstrap token to `<state-dir>/bootstrap-token` with mode
`0600`, created owner-only in one step, and prints `bootstrap_token_file` in its
startup line.

## Readiness gate

At process startup the adapter runs a bounded model preflight before calling
`AgentWorker.start()`:

```
DSH Node start
  -> model preflight (exact DSH_MODEL_OK token, bounded timeout)
  -> model healthy
  -> worker register / heartbeat
  -> available=true
```

Preflight success requires BOTH a non-error process result and an exact
readiness token. A model that exits `0` while returning unexpected or error text
fails readiness, and so does a preflight that exceeds its timeout.

If the model is unavailable, the DSH process stays alive, reports
`model_available: false, reason: "model_unavailable"` to the Bridge, does not
register, does not lease formal tasks, and retries readiness periodically. The
Bridge surfaces this as `available: false, reason: "model_unavailable"`.

Readiness is a model-health report only. It is authenticated with the local
bootstrap token, and it can never make an agent available: availability still
requires a registered, live worker, and a request carrying an `available` field
is rejected.

## Task handling

`handleTask` passes task title, prompt, workspace, cancellation, and follow-up
messages to the real DSH headless runner. Task-state truth is unchanged:
`queued` is not started, a lease is `acknowledged`, and only the real task-handler
invocation boundary becomes `running`.

## Artifacts

After a task runs, the worker reports the outputs the task actually produced:

- **Required** outputs must exist or the task fails. They come from
  `metadata.expected_artifacts` (list of workspace-relative paths) and from the
  known output of a logical task such as `CN-DSH-001`.
- **Prompt-named** files are reported when they exist. The worker extracts
  file-like paths from the task prompt, so a task that produces any requested
  output can return it without a code change; a named file that was not produced
  is skipped rather than failing the task.

A named file that is missing from the workspace is never reported, and artifact
paths are still validated server-side by the Bridge.

## Test

```bash
npm test
npm run check
```

No npm dependencies are required for runtime or tests.
