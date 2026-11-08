export const READINESS_PROMPT = "Return exactly: WORKBUDDY_MODEL_OK";
export const READINESS_TOKEN = "WORKBUDDY_MODEL_OK";
export const MODEL_UNAVAILABLE = "model_unavailable";
export const CREDENTIAL_UNAVAILABLE = "credential_unavailable";
export const DEFAULT_PREFLIGHT_TIMEOUT_MS = 120_000;
export const BOOTSTRAP_TOKEN_REQUIRED = "bootstrap_token_required";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function requireBootstrapToken(value) {
  const token = String(value ?? "").trim();
  if (!token) {
    const error = new Error("WorkBuddy readiness requires AI Team bootstrap credentials");
    error.code = BOOTSTRAP_TOKEN_REQUIRED;
    throw error;
  }
  return token;
}

export function isReadinessResponse(value) {
  return String(value ?? "").trim() === READINESS_TOKEN;
}

export async function runModelPreflight({ runPrompt, timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS } = {}) {
  if (typeof runPrompt !== "function") throw new TypeError("runPrompt is required");
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      const error = new Error(`WorkBuddy readiness timed out after ${timeoutMs}ms`);
      error.code = "readiness_timeout";
      reject(error);
    }, timeoutMs);
  });
  try {
    const result = await Promise.race([
      runPrompt({ signal: controller.signal, timeoutMs }),
      timeout,
    ]);
    const response = result?.response ?? result?.stdout ?? "";
    if (!isReadinessResponse(response)) {
      const error = new Error(
        `WorkBuddy readiness failed: expected ${READINESS_TOKEN}, got ${JSON.stringify(String(response).trim().slice(0, 200))}`,
      );
      error.code = "readiness_token_mismatch";
      throw error;
    }
    return { token: READINESS_TOKEN };
  } finally {
    clearTimeout(timer);
  }
}

function readinessReason(error) {
  return error?.code === CREDENTIAL_UNAVAILABLE ? CREDENTIAL_UNAVAILABLE : MODEL_UNAVAILABLE;
}

export async function waitForModel({ client, preflight, bootstrapToken, retryIntervalMs = 30_000, maxAttempts = Infinity, sleepImpl = sleep, logger = console } = {}) {
  if (!client || typeof client.reportReadiness !== "function") throw new TypeError("client.reportReadiness is required");
  const token = requireBootstrapToken(bootstrapToken);
  if (typeof preflight !== "function") throw new TypeError("preflight is required");
  let attempts = 0;
  while (attempts < maxAttempts) {
    attempts += 1;
    try {
      await preflight();
      await client.reportReadiness({ agentId: "workbuddy", modelAvailable: true, reason: "", bootstrapToken: token });
      return { available: true, attempts };
    } catch (error) {
      const reason = readinessReason(error);
      try {
        await client.reportReadiness({ agentId: "workbuddy", modelAvailable: false, reason, bootstrapToken: token });
      } catch {}
      if (attempts >= maxAttempts) {
        const finalError = new Error(`WorkBuddy model not available after ${attempts} attempt(s): ${reason}`);
        finalError.code = reason;
        throw finalError;
      }
      logger?.warn?.(`WorkBuddy preflight failed (attempt ${attempts}): ${reason}; retrying in ${retryIntervalMs}ms`);
      await sleepImpl(retryIntervalMs);
    }
  }
}

export async function startWorkbuddyWorker({ client, bootstrapToken, preflight, workerFactory, waitForModelImpl = waitForModel, logger = console } = {}) {
  if (!client) throw new TypeError("client is required");
  if (typeof workerFactory !== "function") throw new TypeError("workerFactory is required");
  const token = requireBootstrapToken(bootstrapToken);
  client.bootstrapToken = token;
  const worker = workerFactory();
  try {
    await waitForModelImpl({ client, preflight, bootstrapToken: token, logger });
  } catch (error) {
    logger?.error?.(`WorkBuddy model unavailable: ${error.message}`);
    return { started: false, reason: error.code || "model_unavailable" };
  }
  await worker.register();
  worker.start().catch((error) => {
    logger?.error?.(`WorkBuddy worker loop failed: ${error.message}`);
  });
  return { started: true, worker };
}
