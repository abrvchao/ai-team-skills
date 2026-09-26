import { BridgeClient } from "./client.js";

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  const abort = () => {
    clearTimeout(timer);
    const error = new Error("Aborted");
    error.name = "AbortError";
    reject(error);
  };
  if (signal?.aborted) return abort();
  signal?.addEventListener("abort", abort, { once: true });
});

function messageKey(message) {
  return JSON.stringify([
    message?.role ?? "",
    message?.content ?? "",
    message?.created_at ?? "",
  ]);
}

function normalizeArtifact(value) {
  if (typeof value === "string") return { path: value };
  if (!value || typeof value !== "object") {
    throw new TypeError("Artifact must be a path string or object");
  }
  return {
    path: String(value.path || ""),
    kind: String(value.kind || "file"),
    label: String(value.label || ""),
    metadata: value.metadata && typeof value.metadata === "object"
      ? value.metadata
      : {},
  };
}

export class AgentWorker {
  constructor({
    bridgeUrl = "http://127.0.0.1:8765",
    agentId,
    label = "",
    capabilities = [],
    onTask,
    pollIntervalMs = 1_000,
    heartbeatIntervalMs = 15_000,
    messagePollIntervalMs = 1_000,
    retryMaxMs = 15_000,
    fetchImpl = globalThis.fetch,
    logger = console,
  } = {}) {
    if (!agentId) throw new TypeError("agentId is required");
    if (typeof onTask !== "function") throw new TypeError("onTask must be a function");

    this.agentId = String(agentId);
    this.label = String(label || agentId);
    this.capabilities = [...capabilities].map(String);
    this.onTask = onTask;
    this.pollIntervalMs = Math.max(50, Number(pollIntervalMs) || 1_000);
    this.heartbeatIntervalMs = Math.max(250, Number(heartbeatIntervalMs) || 15_000);
    this.messagePollIntervalMs = Math.max(100, Number(messagePollIntervalMs) || 1_000);
    this.retryMaxMs = Math.max(this.pollIntervalMs, Number(retryMaxMs) || 15_000);
    this.logger = logger;
    this.client = new BridgeClient({ baseUrl: bridgeUrl, fetchImpl });

    this.running = false;
    this.stopRequested = false;
    this.currentAbortController = null;
    this.registration = null;
  }

  async register() {
    this.registration = await this.client.register({
      agentId: this.agentId,
      label: this.label,
      capabilities: this.capabilities,
    });
    return this.registration;
  }

  async start() {
    if (this.running) return;
    this.running = true;
    this.stopRequested = false;
    if (!this.registration) await this.register();

    let backoff = this.pollIntervalMs;
    try {
      while (!this.stopRequested) {
        try {
          const task = await this.client.lease();
          backoff = this.pollIntervalMs;
          if (!task) {
            await this.client.heartbeat();
            await sleep(this.pollIntervalMs);
            continue;
          }
          await this.#execute(task);
        } catch (error) {
          if (this.stopRequested) break;
          this.logger?.warn?.("AI Team worker loop error", error);
          await sleep(backoff);
          backoff = Math.min(this.retryMaxMs, Math.max(this.pollIntervalMs, backoff * 2));
        }
      }
    } finally {
      this.running = false;
      this.currentAbortController = null;
    }
  }

  async runOnce() {
    if (!this.registration) await this.register();
    const task = await this.client.lease();
    if (!task) {
      await this.client.heartbeat();
      return null;
    }
    return this.#execute(task);
  }

  stop() {
    this.stopRequested = true;
    this.currentAbortController?.abort();
  }

  async #execute(task) {
    const taskId = task.task_id;
    const controller = new AbortController();
    this.currentAbortController = controller;

    let messages = Array.isArray(task.messages) ? [...task.messages] : [];
    const seen = new Set(messages.map(messageKey));
    const subscribers = new Set();
    let monitorStopped = false;
    let lastHeartbeat = 0;

    const addArtifact = async (artifact) => {
      const normalized = normalizeArtifact(artifact);
      if (!normalized.path) throw new TypeError("Artifact path is required");
      return this.client.event(taskId, {
        type: "artifact",
        ...normalized,
      });
    };

    const context = {
      task,
      signal: controller.signal,
      get messages() {
        return [...messages];
      },
      getMessages: () => [...messages],
      refreshMessages: async () => {
        const latest = await this.client.messages(taskId);
        const fresh = [];
        for (const item of latest) {
          const key = messageKey(item);
          if (seen.has(key)) continue;
          seen.add(key);
          fresh.push(item);
          messages.push(item);
        }
        for (const item of fresh) {
          for (const handler of subscribers) {
            try {
              await handler(item);
            } catch (error) {
              this.logger?.warn?.("AI Team onMessage handler failed", error);
            }
          }
        }
        return fresh;
      },
      onMessage: (handler) => {
        if (typeof handler !== "function") throw new TypeError("message handler must be a function");
        subscribers.add(handler);
        return () => subscribers.delete(handler);
      },
      addArtifact,
      heartbeat: () => this.client.heartbeat(),
    };

    const monitor = (async () => {
      while (!monitorStopped && !controller.signal.aborted) {
        const now = Date.now();
        try {
          if (now - lastHeartbeat >= this.heartbeatIntervalMs) {
            await this.client.heartbeat();
            lastHeartbeat = now;
          }

          const remoteTask = await this.client.task(taskId);
          if (remoteTask?.status === "cancelled") {
            controller.abort();
            break;
          }
          await context.refreshMessages();
        } catch (error) {
          this.logger?.warn?.("AI Team task monitor error", error);
        }

        try {
          await sleep(this.messagePollIntervalMs, controller.signal);
        } catch (error) {
          if (error?.name !== "AbortError") throw error;
        }
      }
    })();

    try {
      // The lease already proved ACK. This event is emitted at the invocation
      // boundary of the real Node task handler; only now is started_at allowed.
      await this.client.event(taskId, {
        type: "status",
        status: "running",
      });

      let result;
      try {
        result = await this.onTask(context);
      } catch (error) {
        if (controller.signal.aborted) {
          return this.client.task(taskId);
        }
        await this.client.event(taskId, {
          type: "status",
          status: "failed",
          error: error?.message || String(error),
        });
        return this.client.task(taskId);
      }

      if (controller.signal.aborted) {
        return this.client.task(taskId);
      }

      if (Array.isArray(result?.artifacts)) {
        for (const artifact of result.artifacts) {
          await addArtifact(artifact);
        }
      }

      const remote = await this.client.task(taskId);
      if (remote?.status === "cancelled") return remote;

      await this.client.event(taskId, {
        type: "status",
        status: "completed",
      });
      return this.client.task(taskId);
    } finally {
      monitorStopped = true;
      controller.abort();
      await monitor.catch(() => {});
      if (this.currentAbortController === controller) {
        this.currentAbortController = null;
      }
    }
  }
}
