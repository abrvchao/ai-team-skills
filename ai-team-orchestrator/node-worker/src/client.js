export class BridgeError extends Error {
  constructor(message, { status = 0, code = "bridge_error", cause } = {}) {
    super(message, { cause });
    this.name = "BridgeError";
    this.status = status;
    this.code = code;
  }
}

export class BridgeClient {
  constructor({
    baseUrl = "http://127.0.0.1:8765",
    fetchImpl = globalThis.fetch,
    timeoutMs = 10_000,
  } = {}) {
    if (typeof fetchImpl !== "function") {
      throw new TypeError("A fetch implementation is required (Node 18+ recommended)");
    }
    this.baseUrl = String(baseUrl).replace(/\/+$/, "");
    this.fetch = fetchImpl;
    this.timeoutMs = Math.max(100, Number(timeoutMs) || 10_000);
    this.workerId = null;
    this.token = null;
  }

  async #request(method, path, body, { auth = false } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth) {
      if (!this.token) throw new BridgeError("Worker is not registered", { code: "not_registered" });
      headers.Authorization = `Bearer ${this.token}`;
    }

    try {
      const response = await this.fetch(this.baseUrl + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await response.text();
      const payload = text ? JSON.parse(text) : {};
      if (!response.ok) {
        const error = payload?.error || {};
        throw new BridgeError(error.message || `Bridge HTTP ${response.status}`, {
          status: response.status,
          code: error.code || "bridge_http_error",
        });
      }
      return payload;
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      if (error?.name === "AbortError") {
        throw new BridgeError("Bridge request timed out", {
          code: "timeout",
          cause: error,
        });
      }
      throw new BridgeError(error?.message || "Bridge request failed", {
        cause: error,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  async register({ agentId, label = "", capabilities = [] }) {
    const payload = await this.#request("POST", "/workers/register", {
      agent_id: agentId,
      label,
      capabilities,
    });
    this.workerId = payload.worker_id;
    this.token = payload.token;
    return payload;
  }

  async heartbeat() {
    this.#requireRegistration();
    return this.#request(
      "POST",
      `/workers/${encodeURIComponent(this.workerId)}/heartbeat`,
      {},
      { auth: true },
    );
  }

  async lease() {
    this.#requireRegistration();
    const payload = await this.#request(
      "POST",
      `/workers/${encodeURIComponent(this.workerId)}/lease`,
      {},
      { auth: true },
    );
    return payload.task ?? null;
  }

  async task(taskId) {
    return this.#request("GET", `/tasks/${encodeURIComponent(taskId)}`);
  }

  async messages(taskId) {
    this.#requireRegistration();
    const payload = await this.#request(
      "GET",
      `/tasks/${encodeURIComponent(taskId)}/messages`,
      undefined,
      { auth: true },
    );
    return payload.messages ?? [];
  }

  async event(taskId, event) {
    this.#requireRegistration();
    return this.#request(
      "POST",
      `/tasks/${encodeURIComponent(taskId)}/events`,
      event,
      { auth: true },
    );
  }

  clearCredentials() {
    this.workerId = null;
    this.token = null;
  }

  #requireRegistration() {
    if (!this.workerId || !this.token) {
      throw new BridgeError("Worker is not registered", { code: "not_registered" });
    }
  }
}
