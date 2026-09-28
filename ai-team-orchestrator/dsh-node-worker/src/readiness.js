export const READINESS_PROMPT = "仅返回：DSH_MODEL_OK";
export const READINESS_TOKEN = "DSH_MODEL_OK";
export const MODEL_UNAVAILABLE = "model_unavailable";
export const DEFAULT_PREFLIGHT_TIMEOUT_MS = 120_000;
export const BOOTSTRAP_TOKEN_REQUIRED = "bootstrap_token_required";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Readiness reporting is authenticated by the Bridge bootstrap token. Without
 * one, every readiness report is rejected, so the adapter refuses to start
 * instead of looping on 403s. Throws before any readiness loop is entered.
 */
export function requireBootstrapToken(value) {
  const token = String(value ?? "").trim();
  if (!token) {
    const error = new Error(
      "DSH readiness requires a bootstrap token: set AI_TEAM_BOOTSTRAP_TOKEN or " +
        "AI_TEAM_BOOTSTRAP_TOKEN_FILE to the Bridge's bootstrap_token_file before starting the worker",
    );
    error.code = BOOTSTRAP_TOKEN_REQUIRED;
    throw error;
  }
  return token;
}

/** Normalize a model reply so only the exact readiness token passes. */
export function normalizeReadinessOutput(stdout) {
  let text = String(stdout ?? "").trim();
  // Strip one layer of surrounding code/quote formatting only; the token itself
  // contains underscores and must never be rewritten.
  for (const marker of ["`", '"', "'", "*"]) {
    if (text.length >= 2 && text.startsWith(marker) && text.endsWith(marker)) {
      text = text.slice(1, -1).trim();
      break;
    }
  }
  return text;
}

/** A model reply is healthy only when it is exactly the readiness token. */
export function isReadinessResponse(stdout) {
  return normalizeReadinessOutput(stdout) === READINESS_TOKEN;
}

/**
 * Run one bounded model preflight.
 *
 * Success requires BOTH a non-error process result and an exact readiness
 * token; process exit code alone is never sufficient. The timeout aborts the
 * supplied signal and rejects even when the prompt runner ignores it.
 */
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
      const error = new Error(`model readiness timed out after ${timeoutMs}ms`);
      error.code = "readiness_timeout";
      reject(error);
    }, timeoutMs);
  });
  try {
    const result = await Promise.race([
      runPrompt({ prompt: READINESS_PROMPT, signal: controller.signal, timeoutMs }),
      timeout,
    ]);
    if (!isReadinessResponse(result?.stdout)) {
      const error = new Error(
        `model readiness failed: expected ${READINESS_TOKEN}, got ${JSON.stringify(normalizeReadinessOutput(result?.stdout).slice(0, 200))}`,
      );
      error.code = "readiness_token_mismatch";
      throw error;
    }
    return { token: READINESS_TOKEN };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Report model health to the Bridge and only resolve once the model is healthy.
 *
 * The readiness report carries a model-health reason only. It can never mark the
 * agent available; availability still requires a registered, live worker.
 */
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
  // Fail fast: without an authenticated report channel the loop could never
  // succeed, so refuse to enter it.
  const token = requireBootstrapToken(bootstrapToken);
  const runPreflight = preflight ?? (() => runModelPreflight({}));
  let attempts = 0;
  while (attempts < maxAttempts) {
    attempts += 1;
    try {
      await runPreflight();
      await client.reportReadiness({
        agentId: "dsh",
        modelAvailable: true,
        bootstrapToken: token,
      });
      return { ready: true, attempts };
    } catch (error) {
      await client
        .reportReadiness({
          agentId: "dsh",
          modelAvailable: false,
          reason: MODEL_UNAVAILABLE,
          bootstrapToken: token,
        })
        .catch(() => {});
      logger?.error?.(`DSH readiness: ${MODEL_UNAVAILABLE}: ${error.message}`);
      if (attempts >= maxAttempts) return { ready: false, attempts, error };
      await sleepImpl(retryIntervalMs);
    }
  }
  return { ready: false, attempts };
}

/**
 * Gate worker start on model readiness.
 *
 * A worker factory is only invoked after a successful preflight, so no
 * registration can happen for an unhealthy model.
 */
export async function startDshWorker({
  client,
  workerFactory,
  preflight,
  bootstrapToken,
  retryIntervalMs = 30_000,
  maxAttempts = Infinity,
  sleepImpl = sleep,
} = {}) {
  // Fail fast before the readiness loop; a missing token is a configuration
  // error, not a transient model failure.
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
