# ChatGPT Plus via WebCodex local MCP gateway

This is the preferred path when ChatGPT should control the local AI Team
Orchestrator without relying on ChatGPT's native custom write-capable MCP
surface.

## Architecture

```text
ChatGPT Plus
  -> connected WebCodex plugin
  -> WebCodex mcp_tool gateway
  -> Runner-owned local stdio provider
  -> ai-team-orchestrator/mcp_server.py --transport stdio
  -> Agent Bridge http://127.0.0.1:8765
  -> registered DSH Node worker
```

WebCodex owns the local provider process. The AI Team MCP server remains a thin
facade over the Bridge, so task truth stays in the Bridge.

## Prerequisites

- WebCodex Server + Runner are already paired to ChatGPT.
- WebCodex Secure Tunnel is running.
- The WebCodex connection has the local-MCP permission (`mcp:local`).
- AI Team Bridge + DSH worker are running.
- Python used by the provider has `requirements-mcp.txt` installed.

## 1. Prepare a dedicated Python runtime

From `ai-team-orchestrator`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-mcp.txt
```

The provider must use an absolute executable path. On the user's current Mac
workspace this will normally be under the checked-out repository, for example:

```text
/Volumes/brvmac_ssd/dsh/ai-team-skills/ai-team-orchestrator/.venv/bin/python
```

Use the actual canonical path on the machine.

## 2. Register the provider on the WebCodex Runner

Add this block to the Runner's existing `runner.toml` (replace every
`/ABSOLUTE/PATH` placeholder):

```toml
[mcp]
request_timeout_secs = 60

[[mcp.providers]]
id = "ai-team-orchestrator"
name = "AI Team Orchestrator"
executable = "/ABSOLUTE/PATH/ai-team-orchestrator/.venv/bin/python"
args = [
  "/ABSOLUTE/PATH/ai-team-orchestrator/mcp_server.py",
  "--transport",
  "stdio",
]
cwd = "/ABSOLUTE/PATH/ai-team-orchestrator"
timeout_secs = 60
```

The Orchestrator Bridge already defaults to `http://127.0.0.1:8765`, so no
extra environment mapping is needed for the normal local setup.

If the Bridge uses another URL, export `AGENT_BRIDGE_URL` in the Runner host
environment and add:

```toml
env_from_env = { AGENT_BRIDGE_URL = "AGENT_BRIDGE_URL" }
```

WebCodex supports hot reload of local MCP providers. If the local runtime reports
that restart is required, restart only the Runner and keep the Bridge/DSH
processes running.

## 3. Start/restore the WebCodex tunnel

For WebCodex Desktop on macOS:

1. Open WebCodex Desktop.
2. Confirm the local Server, Runner, and project are ready.
3. Open **Connection**.
4. Select **OpenAI Secure Tunnel**.
5. Click **Start secure tunnel**.

Starting a connection method and actually starting the tunnel are separate
actions.

## 4. Verify from ChatGPT

The WebCodex gateway exposes one outer tool named `mcp_tool`.

Expected sequence:

```text
mcp_tool(action="list")
  -> contains server id "ai-team-orchestrator"

mcp_tool(action="describe", server="ai-team-orchestrator")
  -> list_agents, list_tasks, get_task, list_artifacts,
     submit_task, send_message, cancel_task

mcp_tool(action="call", server="ai-team-orchestrator",
         tool="list_agents", arguments={})
  -> dsh available=true
```

Then prove write access with a new smoke task:

```text
mcp_tool(action="call",
         server="ai-team-orchestrator",
         tool="submit_task",
         arguments={
           "agent_id": "dsh",
           "title": "ChatGPT Plus WebCodex smoke",
           "prompt": "Create webcodex-ai-team-proof.txt and return it as an artifact.",
           "workspace": "."
         })
```

Use the returned task id with `get_task` and `list_artifacts`. Do not say DSH
started until the Bridge reports `started=true` / `running`.

## Failure modes

- `Tunnel-client has not been seen for 300 seconds`: start/restart the WebCodex
  Secure Tunnel.
- `FORBIDDEN` for local MCP gateway: reconnect/authorize WebCodex with
  `mcp:local` permission.
- provider not listed: check the Runner `[mcp]` block and absolute executable,
  args, and cwd paths.
- provider listed but describe fails: run the configured provider command
  manually and verify the Python environment has `requirements-mcp.txt`.
- list_agents works but DSH unavailable: repair Bridge/DSH readiness; do not
  treat the MCP bridge as the worker health source.
