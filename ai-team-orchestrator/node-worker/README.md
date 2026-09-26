# Node Worker SDK

Zero-runtime-dependency Node.js adapter for AI Team Orchestrator.

Designed for existing Node agents such as DSH. It uses Node 18+ native `fetch`
and hides worker registration, heartbeat, task leasing, status events,
follow-up messages, cancellation, and artifact reporting.

## Minimal DSH integration

```js
import { AgentWorker } from "./node-worker/src/index.js";
import { handleTask } from "./src/dsh-task-runner.js";

const worker = new AgentWorker({
  bridgeUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
  agentId: "dsh",
  label: "DSH",
  capabilities: ["implementation", "testing", "debugging", "git"],

  onTask: async (ctx) => {
    return handleTask({
      title: ctx.task.title,
      prompt: ctx.task.prompt,
      workspace: ctx.task.workspace,
      signal: ctx.signal,
      messages: ctx.getMessages,
      onMessage: ctx.onMessage,
      addArtifact: ctx.addArtifact,
    });
  },
});

await worker.start();
```

## Truth semantics

The Bridge lease has already moved a task from `queued` to `acknowledged`.

The SDK sends `running` immediately at the invocation boundary of the
configured `onTask` handler. Therefore:

- offline / queued: not started;
- leased / acknowledged: not started;
- `onTask` invocation boundary: running / started;
- handler resolves: completed;
- handler rejects: failed;
- remote cancellation: `AbortSignal` is aborted and SDK does not overwrite the
  cancelled state.

## Task context

```js
ctx.task
ctx.signal
ctx.messages
ctx.getMessages()
ctx.refreshMessages()
ctx.onMessage(handler)
ctx.addArtifact({ path, kind, label, metadata })
ctx.heartbeat()
```

A task handler may also return:

```js
return {
  artifacts: [
    { path: "output/report.json", kind: "file", label: "result" }
  ]
}
```

Artifact path validation remains server-side in the Bridge.

## Test

```bash
npm test
npm run check
```

No npm dependencies are required for runtime or tests.
