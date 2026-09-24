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

## Registered pull workers — preferred long-running mode

V0.3 removes the requirement that the Supervisor/Bridge know the local DSH
executable.

The preferred runtime is now:

```
ChatGPT / Supervisor
        ↓
      MCP
        ↓
Agent Bridge
        ↓
queued task
        ↓
DSH pull_worker.py  ← register / heartbeat / lease
        ↓
real DSH process
```

This changes the operational model:

- the Bridge can accept a DSH task while no DSH worker is online;
- that task remains `queued`, `started=false`;
- a registered DSH worker leases the task;
- lease = worker ACK → `acknowledged`;
- `started_at` is still empty after lease;
- only after the worker successfully starts the real configured process does it
  report `running`;
- only then may the Supervisor say “DSH has started.”

### One-time DSH worker setup

The exact local DSH command is machine-specific and is configured **only on the
DSH machine**, not in ChatGPT, MCP, task payloads, or GitHub Issues.

Example command shape:

```bash
cd ai-team-orchestrator

export DSH_INNER_COMMAND_JSON='[
  "/absolute/path/to/dsh",
  "run",
  "--prompt-file",
  "{prompt_file}"
]'

python pull_worker.py \
  --bridge http://127.0.0.1:8765 \
  --agent-id dsh \
  --label "DSH Mac worker"
```

The worker command may use these placeholders:

```
{task_id}
{workspace}
{request_file}
{prompt_file}
{message_file}
{stdout_file}
{stderr_file}
{artifact_manifest}
{task_dir}
```

Once this process is kept running, future tasks require no manual copy/paste.

### Pull-worker bridge protocol

Worker registration:

```
POST /workers/register
```

Worker lifecycle:

```
POST /workers/{worker_id}/heartbeat
POST /workers/{worker_id}/lease
GET  /workers/{worker_id}
GET  /tasks/{task_id}/messages
POST /tasks/{task_id}/events
```

Registration returns a bearer token once. The bridge stores only its hash.
Worker tokens cannot control tasks leased to a different worker.

The bridge remains bound to localhost by default. Do not expose the worker
registration API directly to the public Internet.

### Agent availability semantics

`list_agents` now distinguishes:

```
configured  = an execution transport can accept/queue tasks
queueable   = submit_task may persist a queued task
available   = push-local execution exists OR a live pull worker heartbeat exists
```

Therefore this is valid and intentional:

```json
{
  "agent_id": "dsh",
  "configured": true,
  "queueable": true,
  "available": false
}
```

It means DSH can receive queued work, but **no live DSH worker is online**.

### Follow-up messages

The worker periodically mirrors Supervisor messages into:

```
<workspace>/.ai-team-worker/<task_id>/messages.jsonl
```

The configured agent process is told where that file is located. Agents that
support live instruction polling can consume it while running.

### Artifacts

The worker always returns stdout/stderr as review artifacts for an execution
that actually started.

An agent may optionally append JSONL artifact declarations to:

```
<workspace>/.ai-team-worker/<task_id>/artifacts.jsonl
```

Example:

```json
{"path":"path/to/file","kind":"file","label":"implementation"}
```

The Bridge still enforces that artifact paths remain inside the task workspace.

## MCP facade for ChatGPT / Codex

V0.2 exposes the same control plane as focused MCP tools:

```
list_agents       # read
list_tasks        # read
get_task          # read
list_artifacts    # read
submit_task       # write/action
send_message      # write/action
cancel_task       # destructive action
```

The MCP layer delegates to the local bridge. It does not launch DSH itself.

Install the official MCP Python SDK:

```bash
python -m pip install -r requirements-mcp.txt
```

Start the local bridge first:

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
  --workspace-root /Volumes/brvmac_ssd/dsh
```

Then start the MCP facade:

```bash
export AGENT_BRIDGE_URL=http://127.0.0.1:8765
python mcp_server.py
```

The Streamable HTTP MCP endpoint is:

```
http://127.0.0.1:3000/mcp
```

Inspect locally:

```bash
npx @modelcontextprotocol/inspector@latest
```

Choose **Streamable HTTP** and enter:

```
http://127.0.0.1:3000/mcp
```

### Why ChatGPT cannot use localhost directly

Hosted ChatGPT does not connect directly to a local MCP server. For a private
Mac-hosted server, use OpenAI Secure MCP Tunnel (or deploy a remote HTTPS MCP
endpoint).

### Secure MCP Tunnel runbook

Prerequisites:

- an OpenAI Platform `tunnel_id`;
- a runtime API key permitted to use that tunnel;
- ChatGPT developer-mode/plugin access appropriate for the target workspace;
- `tunnel-client` installed on the Mac that can reach the local MCP server.

Initialize a local HTTP MCP tunnel profile:

```bash
export CONTROL_PLANE_API_KEY="sk-..."

tunnel-client init \
  --profile ai-team-local \
  --tunnel-id tunnel_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  --mcp-server-url http://127.0.0.1:3000/mcp

tunnel-client doctor --profile ai-team-local --explain
tunnel-client run --profile ai-team-local
```

Keep the tunnel client running.

When creating the ChatGPT developer-mode plugin/app, choose **Tunnel** and
select/paste the same `tunnel_id`.

Only after ChatGPT can list these MCP tools:

```
list_agents
submit_task
get_task
send_message
list_artifacts
cancel_task
```

is the ChatGPT → Orchestrator path connected.

Only after a real `submit_task(agent_id="dsh", ...)` produces a task whose
bridge-derived state changes:

```
queued
→ acknowledged
→ running
```

may the Supervisor say “DSH has started.”

The final chain is:

```
ChatGPT
   ↓ MCP tool call
Secure MCP Tunnel
   ↓
mcp_server.py
   ↓
bridge.py
   ↓
dsh_cli_wrapper.py
   ↓
real DSH CLI
   ↓
workspace / git / tests / artifacts
```

Secrets such as the tunnel runtime key and DSH/provider credentials stay in the
local runtime environment and are not task fields or tool results.

## Next

After DSH is connected with its real local CLI command:

1. Dogfood the orchestrator on Content Opportunity Radar.
2. Add Gemini adapter behind the same interface.
3. Add Grok adapter.
4. Add multi-agent plans, dependencies, review/retry policies.
5. Move this subproject into its own `abrvchao/ai-team-orchestrator` repository
   when repository-creation access is available.
