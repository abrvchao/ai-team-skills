// WorkBuddy 就绪模块（Readiness）。
//
// 职责：
//   1. 校验 AI Team bootstrap token（本地就绪上报的凭据，不是模型凭据）。
//   2. 通过真实 CLI 调用做「模型可用性预检」（preflight），只有真的答对才报告 available。
//   3. 把预检失败分类成可辨识的错误码，供 Supervisor 判断。
//
// 重要约定：
//   - 认证失败（auth_unavailable）属于「配置问题」，绝不无限重试；
//   - 限流（rate_limited）可以退避重试；
//   - 任何情况下都不会在日志里打印凭据。

export const READINESS_PROMPT = "Respond exactly WORKBUDDY_MODEL_OK";
export const READINESS_TOKEN = "WORKBUDDY_MODEL_OK";
export const DEFAULT_PREFLIGHT_TIMEOUT_MS = 120_000;

// 错误码（供 Supervisor / 运维识别）
export const BOOTSTRAP_TOKEN_REQUIRED = "bootstrap_token_required";
export const MODEL_UNAVAILABLE = "model_unavailable";
export const AUTH_UNAVAILABLE = "auth_unavailable";
export const RATE_LIMITED = "rate_limited";
export const READINESS_TOKEN_MISMATCH = "readiness_token_mismatch";
export const READINESS_TIMEOUT = "readiness_timeout";

// 启动预检可能产生模型费用；调用方不能通过 maxAttempts 提高次数。
export const MAX_PREFLIGHT_ATTEMPTS = 1;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * WorkBuddy 的就绪上报需要 AI Team 的 bootstrap token。
 * 缺失时直接抛出，避免 Worker 在「未授权」状态下假装上线。
 */
export function requireBootstrapToken(value) {
  const token = String(value ?? "").trim();
  if (!token) {
    const error = new Error("WorkBuddy readiness requires AI Team bootstrap credentials");
    error.code = BOOTSTRAP_TOKEN_REQUIRED;
    throw error;
  }
  return token;
}

/** 预检返回值是否等于约定 token（严格全等，避免模型多写解释被误判通过）。 */
export function isReadinessResponse(value) {
  return String(value ?? "").trim() === READINESS_TOKEN;
}

/**
 * 执行一次模型预检。
 * runPrompt 由调用方注入，便于测试时替换（不会真的起 CLI 进程）。
 */
export async function runModelPreflight({ runPrompt, timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS } = {}) {
  if (typeof runPrompt !== "function") throw new TypeError("runPrompt is required");
  const controller = new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      const error = new Error(`WorkBuddy readiness timed out after ${timeoutMs}ms`);
      error.code = READINESS_TIMEOUT;
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
      error.code = READINESS_TOKEN_MISMATCH;
      throw error;
    }
    return { token: READINESS_TOKEN };
  } finally {
    clearTimeout(timer);
  }
}

/** 预检失败 → 上报给 Bridge 的 reason。 */
export function readinessReason(error) {
  if (error?.code === AUTH_UNAVAILABLE) return AUTH_UNAVAILABLE;
  if (error?.code === RATE_LIMITED) return RATE_LIMITED;
  return MODEL_UNAVAILABLE;
}

/** 认证失败不该重试：重试一万次也还是没登录，只会刷屏 + 触发风控。 */
export function isRetryableReadinessError(error) {
  return error?.code !== AUTH_UNAVAILABLE && error?.code !== BOOTSTRAP_TOKEN_REQUIRED;
}

/**
 * 循环等待模型可用。
 *
 * - 预检通过 → 上报 model_available=true，返回 ready。
 * - 预检失败 → 上报 model_available=false + reason；可重试的错误才退避重试。
 * - 认证失败 → 立即返回（不重试），reason=auth_unavailable。
 */
export async function waitForModel({
  client,
  preflight,
  bootstrapToken,
  agentId = "workbuddy",
  retryIntervalMs = 30_000,
  sleepImpl = sleep,
  logger = console,
} = {}) {
  if (!client || typeof client.reportReadiness !== "function") {
    throw new TypeError("client.reportReadiness is required");
  }
  const token = requireBootstrapToken(bootstrapToken);
  if (typeof preflight !== "function") throw new TypeError("preflight is required");
  let attempts = 0;
  while (attempts < MAX_PREFLIGHT_ATTEMPTS) {
    attempts += 1;
    try {
      await preflight();
      await client.reportReadiness({ agentId, modelAvailable: true, bootstrapToken: token });
      return { ready: true, attempts };
    } catch (error) {
      const reason = readinessReason(error);
      await client
        .reportReadiness({ agentId, modelAvailable: false, reason, bootstrapToken: token })
        .catch(() => {});
      logger?.error?.(`WorkBuddy readiness: ${reason}: ${error?.message || error}`);
      if (!isRetryableReadinessError(error)) return { ready: false, attempts, error, reason };
      if (attempts >= MAX_PREFLIGHT_ATTEMPTS) return { ready: false, attempts, error, reason };
      await sleepImpl(retryIntervalMs);
    }
  }
  return { ready: false, attempts };
}

/** 就绪通过后注册并启动 Worker。 */
export async function startWorkBuddyWorker({
  client,
  workerFactory,
  preflight,
  bootstrapToken,
  retryIntervalMs = 30_000,
  sleepImpl = sleep,
} = {}) {
  requireBootstrapToken(bootstrapToken);
  const readiness = await waitForModel({
    client,
    preflight,
    bootstrapToken,
    retryIntervalMs,
    sleepImpl,
  });
  if (!readiness.ready) return { started: false, readiness };
  if (typeof workerFactory !== "function") throw new TypeError("workerFactory is required");
  const worker = workerFactory();
  await worker.start();
  return { started: true, worker, readiness };
}
