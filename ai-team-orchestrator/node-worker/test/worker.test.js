import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";

import { AgentWorker } from "../src/index.js";

const readJson = async (req) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
};

const json = (res, status, payload) => {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
};

class FakeBridge {
  constructor() {
    this.server = null;
    this.url = null;
    this.token = "worker-secret";
    this.workerId = "worker_1";
    this.tasks = [];
    this.messages = new Map();
    this.artifacts = new Map();
    this.events = [];
    this.heartbeats = 0;
  }

  async start() {
    this.server = createServer(async (req, res) => {
      try {
        const url = new URL(req.url, "http://127.0.0.1");
        const path = url.pathname;

        if (req.method === "POST" && path === "/workers/register") {
          const body = await readJson(req);
          return json(res, 201, {
            worker_id: this.workerId,
            agent_id: body.agent_id,
            label: body.label,
            capabilities: body.capabilities,
            token: this.token,
          });
        }

        const authRequired =
          path.startsWith("/workers/") ||
          /\/tasks\/[^/]+\/(events|messages)$/.test(path);
        if (authRequired && req.headers.authorization !== `Bearer ${this.token}`) {
          return json(res, 403, { error: { code: "forbidden", message: "bad token" } });
        }

        if (
          req.method === "POST" &&
          path === `/workers/${this.workerId}/heartbeat`
        ) {
          this.heartbeats += 1;
          return json(res, 200, {
            worker_id: this.workerId,
            online: true,
          });
        }

        if (
          req.method === "POST" &&
          path === `/workers/${this.workerId}/lease`
        ) {
          const task = this.tasks.find((item) => item.status === "queued");
          if (!task) return json(res, 200, { task: null });
          task.status = "acknowledged";
          task.worker_id = this.workerId;
          task.acknowledged_at = new Date().toISOString();
          return json(res, 200, {
            task: {
              ...structuredClone(task),
              messages: [...(this.messages.get(task.task_id) || [])],
            },
          });
        }

        const taskMatch = path.match(/^\/tasks\/([^/]+)$/);
        if (req.method === "GET" && taskMatch) {
          const task = this.#task(decodeURIComponent(taskMatch[1]));
          return json(res, 200, structuredClone(task));
        }

        const msgMatch = path.match(/^\/tasks\/([^/]+)\/messages$/);
        if (req.method === "GET" && msgMatch) {
          const taskId = decodeURIComponent(msgMatch[1]);
          return json(res, 200, {
            task_id: taskId,
            messages: [...(this.messages.get(taskId) || [])],
          });
        }

        const eventMatch = path.match(/^\/tasks\/([^/]+)\/events$/);
        if (req.method === "POST" && eventMatch) {
          const taskId = decodeURIComponent(eventMatch[1]);
          const task = this.#task(taskId);
          const event = await readJson(req);
          this.events.push({ taskId, event: structuredClone(event) });

          if (event.type === "status") {
            if (event.status === "running") {
              task.status = "running";
              task.started = true;
              task.started_at ||= new Date().toISOString();
            } else if (event.status === "completed") {
              task.status = "completed";
              task.finished_at = new Date().toISOString();
            } else if (event.status === "failed") {
              task.status = "failed";
              task.error = event.error;
              task.finished_at = new Date().toISOString();
            }
          }

          if (event.type === "artifact") {
            const items = this.artifacts.get(taskId) || [];
            items.push(structuredClone(event));
            this.artifacts.set(taskId, items);
          }

          return json(res, 200, structuredClone(task));
        }

        return json(res, 404, { error: { code: "not_found", message: path } });
      } catch (error) {
        return json(res, 500, {
          error: { code: "fake_bridge_error", message: error.message },
        });
      }
    });

    await new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    const address = this.server.address();
    this.url = `http://127.0.0.1:${address.port}`;
    return this;
  }

  async close() {
    if (!this.server) return;
    await new Promise((resolve, reject) =>
      this.server.close((error) => (error ? reject(error) : resolve())),
    );
  }

  enqueue(overrides = {}) {
    const task = {
      task_id: overrides.task_id || `task_${this.tasks.length + 1}`,
      agent_id: "dsh",
      title: "Test task",
      prompt: "Do the work",
      workspace: ".",
      status: "queued",
      started: false,
      started_at: null,
      acknowledged_at: null,
      finished_at: null,
      ...overrides,
    };
    this.tasks.push(task);
    this.messages.set(task.task_id, []);
    return task;
  }

  pushMessage(taskId, content) {
    const items = this.messages.get(taskId) || [];
    items.push({
      role: "supervisor",
      content,
      created_at: new Date().toISOString(),
    });
    this.messages.set(taskId, items);
  }

  cancel(taskId) {
    const task = this.#task(taskId);
    task.status = "cancelled";
    task.finished_at = new Date().toISOString();
  }

  #task(taskId) {
    const task = this.tasks.find((item) => item.task_id === taskId);
    if (!task) throw new Error(`missing task ${taskId}`);
    return task;
  }
}

const withBridge = async (fn) => {
  const bridge = await new FakeBridge().start();
  try {
    await fn(bridge);
  } finally {
    await bridge.close();
  }
};

const workerFor = (bridge, onTask, overrides = {}) =>
  new AgentWorker({
    bridgeUrl: bridge.url,
    agentId: "dsh",
    label: "DSH Node",
    capabilities: ["implementation", "testing"],
    onTask,
    pollIntervalMs: 30,
    heartbeatIntervalMs: 40,
    messagePollIntervalMs: 25,
    retryMaxMs: 100,
    logger: { warn() {} },
    ...overrides,
  });

test("register → lease ACK → running at handler boundary → completed", async () => {
  await withBridge(async (bridge) => {
    const task = bridge.enqueue();
    let enteredState;

    const worker = workerFor(bridge, async (ctx) => {
      enteredState = structuredClone(
        bridge.tasks.find((item) => item.task_id === ctx.task.task_id),
      );
      return {};
    });

    const result = await worker.runOnce();

    assert.equal(result.status, "completed");
    assert.equal(enteredState.status, "running");
    assert.equal(enteredState.started, true);
    assert.ok(enteredState.acknowledged_at);
    assert.ok(enteredState.started_at);

    const statuses = bridge.events
      .filter(({ taskId, event }) => taskId === task.task_id && event.type === "status")
      .map(({ event }) => event.status);
    assert.deepEqual(statuses, ["running", "completed"]);
  });
});

test("handler rejection reports failed exactly once", async () => {
  await withBridge(async (bridge) => {
    const task = bridge.enqueue();
    const worker = workerFor(bridge, async () => {
      throw new Error("DSH task failed");
    });

    const result = await worker.runOnce();

    assert.equal(result.status, "failed");
    assert.equal(result.error, "DSH task failed");
    const failures = bridge.events.filter(
      ({ taskId, event }) =>
        taskId === task.task_id &&
        event.type === "status" &&
        event.status === "failed",
    );
    assert.equal(failures.length, 1);
  });
});

test("returned artifacts are reported before completion", async () => {
  await withBridge(async (bridge) => {
    const task = bridge.enqueue();
    const worker = workerFor(bridge, async () => ({
      artifacts: [
        {
          path: "result.json",
          kind: "file",
          label: "DSH result",
          metadata: { tests: "passed" },
        },
      ],
    }));

    const result = await worker.runOnce();

    assert.equal(result.status, "completed");
    const artifacts = bridge.artifacts.get(task.task_id);
    assert.equal(artifacts.length, 1);
    assert.equal(artifacts[0].path, "result.json");
    assert.equal(artifacts[0].label, "DSH result");

    const eventTypes = bridge.events
      .filter(({ taskId }) => taskId === task.task_id)
      .map(({ event }) => event.type === "status" ? event.status : event.type);
    assert.deepEqual(eventTypes, ["running", "artifact", "completed"]);
  });
});

test("follow-up messages are delivered incrementally", async () => {
  await withBridge(async (bridge) => {
    const task = bridge.enqueue();
    let received;

    const worker = workerFor(bridge, async (ctx) => {
      received = new Promise((resolve) => {
        ctx.onMessage((message) => resolve(message.content));
      });
      bridge.pushMessage(task.task_id, "Please also run integration tests.");
      const content = await received;
      assert.equal(content, "Please also run integration tests.");
    });

    const result = await worker.runOnce();
    assert.equal(result.status, "completed");
  });
});

test("remote cancellation aborts the handler and does not report failure/completion", async () => {
  await withBridge(async (bridge) => {
    const task = bridge.enqueue();
    let observedAbort = false;

    const worker = workerFor(bridge, async (ctx) => {
      bridge.cancel(task.task_id);
      await new Promise((resolve) => {
        if (ctx.signal.aborted) {
          observedAbort = true;
          return resolve();
        }
        ctx.signal.addEventListener("abort", () => {
          observedAbort = true;
          resolve();
        }, { once: true });
      });
    });

    const result = await worker.runOnce();

    assert.equal(result.status, "cancelled");
    assert.equal(observedAbort, true);
    const terminalEvents = bridge.events.filter(
      ({ taskId, event }) =>
        taskId === task.task_id &&
        event.type === "status" &&
        ["completed", "failed"].includes(event.status),
    );
    assert.equal(terminalEvents.length, 0);
  });
});

test("empty lease heartbeats and leaves no task side effects", async () => {
  await withBridge(async (bridge) => {
    let called = false;
    const worker = workerFor(bridge, async () => {
      called = true;
    });

    const result = await worker.runOnce();

    assert.equal(result, null);
    assert.equal(called, false);
    assert.equal(bridge.heartbeats, 1);
    assert.equal(bridge.events.length, 0);
  });
});

test("worker holds registration token in memory and can stop without persisting it", async () => {
  await withBridge(async (bridge) => {
    const worker = workerFor(bridge, async () => {});
    await worker.register();

    assert.equal(worker.client.workerId, bridge.workerId);
    assert.equal(worker.client.token, bridge.token);
    worker.client.clearCredentials();
    assert.equal(worker.client.workerId, null);
    assert.equal(worker.client.token, null);
  });
});
