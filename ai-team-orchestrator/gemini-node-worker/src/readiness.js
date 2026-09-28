export const READINESS_PROMPT = "Return exactly: GEMINI_MODEL_OK";
export const READINESS_TOKEN = "GEMINI_MODEL_OK";
export const MODEL_UNAVAILABLE = "model_unavailable";
export const DEFAULT_PREFLIGHT_TIMEOUT_MS = 120_000;
export const BOOTSTRAP_TOKEN_REQUIRED = "bootstrap_token_required";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function requireBootstrapToken(value) {
  const token = String(value ?? "").trim();
  if (!token) {
    const error = new Error(
      "Gemini readiness requires a bootstrap token: set AI_TEAM_BOOTSTRAP_TOKEN or " +
        "AI_TEAM_BOOTSTRAP_TOKEN_FILE before starting the worker",
    );
    error.code = BOOTSTRAP_TOKEN_REQUIRED;
    throw error;
  }
  return token;
}

export function normalizeReadinessOutput(value) {
  return String(value ?? "").trim();
}

export function isReadinessResponse(value) {
  return normalizeReadinessOutput(value) === READINESS_TOKEN;
}

export async function runModelPreflight({
  runPrompt,
  timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS,
} = {}) {
  if (typeof runPrompt !== "function") throw new TypeError("runPrompt is required");
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      const error = new Error(`Gemini readiness timed out after ${timeoutMs}ms`);
      error.code = "readiness_timeout";
      reject(error);
    }, timeoutMs);
  });
  try {
    const result = await Promise.race([
      runPrompt({ prompt: READINESS_PROMPT, signal: controller.signal, timeoutMs }),
      timeout,
    ]);
    const response = result?.response ?? result?.stdout ?? "";
    if (!isReadinessResponse(response)) {
      const error = new Error(
        `Gemini readiness failed: expected ${READINESS_TOKEN}, got ${JSON.stringify(
          normalizeReadinessOutput(response).slice(0, 200),
        )}`,
      );
      error.code = "readiness_token_mismatch";
      throw error;
    }
    return { token: READINESS_TOKEN };
  } finally {
    clearTimeout(timer);
  }
}

export async function waitForModel({
  client,
  preflight,
  bootstrapToken,
  retryIntervalMs = 30_000,
  maxAttempts = Infinity,
  sleepImpl = sleep,
  logger = console,
} = {}) {
  if (!client || typeof client.reportReadiness !== "function") {
    throw new TypeError("client.reportReadiness is required");
  }
  const token = requireBootstrapToken(bootstrapToken);
  if (typeof preflight !== "function") throw new TypeError("preflight is required");

  let attempts = 0;
  while (attempts < maxAttempts) {
    attempts += 1;
    try {
      await preflight();
      await client.reportReadiness({
        agentId: "gemini",
        modelAvailable: true,
        bootstrapToken: token,
      });
      return { ready: true, attempts };
    } catch (error) {
      await client
        .reportReadiness({
          agentId: "gemini",
          modelAvailable: false,
          reason: MODEL_UNAVAILABLE,
          bootstrapToken: token,
        })
        .catch(() => {});
      logger?.error?.(`Gemini readiness: ${MODEL_UNAVAILABLE}: ${error.message}`);
      if (attempts >= maxAttempts) return { ready: false, attempts, error };
      await sleepImpl(retryIntervalMs);
    }
  }
  return { ready: false, attempts };
}

export async function startGeminiWorker({
  client,
  workerFactory,
  preflight,
  bootstrapToken,
  retryIntervalMs = 30_000,
  maxAttempts = Infinity,
  sleepImpl = sleep,
} = {}) {
  requireBootstrapToken(bootstrapToken);
  const readiness = await waitForModel({
    client,
    preflight,
    bootstrapToken,
    retryIntervalMs,
    maxAttempts,
    sleepImpl,
  });
  if (!readiness.ready) return { started: false, readiness };
  if (typeof workerFactory !== "function") throw new TypeError("workerFactory is required");
  const worker = workerFactory();
  await worker.start();
  return { started: true, worker, readiness };
}
