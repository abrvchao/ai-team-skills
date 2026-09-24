# AI Team Orchestrator

AI Team Orchestrator is the control plane for a real multi-agent team.

The first release focuses on one hard requirement:

> The Supervisor must be able to dispatch a real task to DSH and verify whether
> DSH actually acknowledged/started it.

GitHub is not the execution bus. GitHub remains a repository, PR, and artifact
system.

## Status truth

The orchestrator uses these task states:

```
queued
acknowledged
running
blocked
completed
failed
cancelled
```

Truth rules:

- `queued` means **not started**.
- A task becomes `acknowledged` only after a worker protocol event.
- `started_at` is populated only after a worker reports `running`.
- A process exiting without ACK becomes `failed` with `started=false`.
- `completed` requires both:
  - worker emitted `completed`;
  - worker process exited with code 0.

The Supervisor must never report “DSH started” while `started=false`.

## Architecture

```
User
  ↓
Supervisor
  ↓
Dispatcher
  ↓
Agent Registry
  ├─ DSHAdapter      → local Agent Bridge → DSH CLI
  ├─ GeminiAdapter   → not configured yet
  ├─ GrokAdapter     → not configured yet
  └─ CodexAdapter    → not configured yet

TaskStore (SQLite)
  ├─ tasks
  ├─ messages
  └─ artifacts
```

The public adapter contract is:

```python
submit(task) -> task_id
status(task_id)
message(task_id, message)
artifacts(task_id)
cancel(task_id)
```

## Bridge API

Default bind address is local-only:

```
http://127.0.0.1:8765
```

Endpoints:

```
GET  /health
GET  /agents
GET  /tasks
POST /tasks
GET  /tasks/{id}
POST /tasks/{id}/messages
GET  /tasks/{id}/artifacts
POST /tasks/{id}/cancel
```

The bridge never accepts an executable/command from a task request. The DSH
command is configured only when the bridge starts, and workers run with
`shell=False`.

Task workspaces are constrained to one configured workspace root.

## Run the fake DSH end-to-end demo

From this directory:

```bash
python bridge.py \
  --workspace-root .. \
  --dsh-command '["python","fake_worker.py","{request_file}"]'
```

Submit a task:

```bash
curl -s http://127.0.0.1:8765/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "dsh",
    "title": "Implement a test change",
    "prompt": "Create one result artifact.",
    "workspace": "."
  }'
```

The response contains a real `task_id`.

Check status:

```bash
curl -s http://127.0.0.1:8765/tasks/<task_id>
```

Expected completed result includes:

```json
{
  "agent_id": "dsh",
  "status": "completed",
  "started": true,
  "started_at": "...",
  "finished_at": "..."
}
```

Artifacts:

```bash
curl -s http://127.0.0.1:8765/tasks/<task_id>/artifacts
```

## Connect the real DSH CLI

The bridge supports arbitrary DSH CLIs through `dsh_cli_wrapper.py`.

The wrapper converts a normal CLI process into the worker event protocol:

```
ack
running
completed / failed
```

The real DSH executable is configured as argv JSON, never as a task field.

Example shape:

```bash
export DSH_INNER_COMMAND_JSON='[
  "/absolute/path/to/dsh",
  "run",
  "--prompt-file",
  "{prompt_file}"
]'

export DSH_COMMAND_JSON='[
  "python",
  "dsh_cli_wrapper.py",
  "{request_file}"
]'

python bridge.py \
  --workspace-root /path/to/allowed/workspaces
```

Supported inner-command placeholders:

```
{request_file}
{prompt_file}
{workspace}
{task_dir}
{task_id}
{message_file}
```

The exact DSH CLI launch argv is intentionally not guessed. Once the real local
DSH command is known, it is the only machine-specific binding required to make
the DSH adapter real.

## Worker protocol

A protocol-aware worker may write JSONL events directly to stdout:

```json
{"type":"ack"}
{"type":"status","status":"running"}
{"type":"artifact","path":"relative/path","kind":"file"}
{"type":"status","status":"completed"}
```

Normal non-JSON worker output is stored in task logs and does not affect task
truth.

Follow-up Supervisor messages are persisted in:

```
<task_dir>/messages.jsonl
```

A DSH wrapper/worker may tail or re-read that file during execution.

## Agent Registry

V0.1 registers:

| Agent | State | Transport |
|---|---|---|
| DSH | available when command configured | local bridge |
| Gemini | not configured | placeholder |
| Grok | not configured | placeholder |
| Codex | not configured | placeholder |

Unavailable agents fail dispatch explicitly. They never pretend to be running.

## JSON schemas

Protocol schemas live in:

```
schemas/task.schema.json
schemas/status.schema.json
schemas/artifact.schema.json
```

## Test

```bash
python -m compileall -q .
python -m unittest discover -v
```

Tests execute a real subprocess fake worker and verify:

- submit → ACK → running → completed;
- `started_at` only after running;
- worker with no ACK never becomes started;
- worker cannot claim completion and exit non-zero;
- artifacts;
- follow-up messages;
- cancel;
- workspace traversal rejection;
- credential-shaped task metadata rejection;
- Supervisor HTTP client;
- Gemini/Grok/Codex placeholders remain unavailable.

## Next

After DSH is connected with its real local CLI command:

1. Dogfood the orchestrator on Content Opportunity Radar.
2. Add Gemini adapter behind the same interface.
3. Add Grok adapter.
4. Add multi-agent plans, dependencies, review/retry policies.
5. Move this subproject into its own `abrvchao/ai-team-orchestrator` repository
   when repository-creation access is available.
