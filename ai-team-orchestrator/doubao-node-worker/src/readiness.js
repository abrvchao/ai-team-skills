export const READINESS_PROMPT = "Return exactly: DOUBAO_MODEL_OK";
export const READINESS_TOKEN = "DOUBAO_MODEL_OK";
export const MODEL_UNAVAILABLE = "model_unavailable";
export const CREDENTIAL_UNAVAILABLE = "credential_unavailable";
// 火山方舟 SetLimitExceeded 表示触发设置的额度限制，不应自动重复发请求。
export const QUOTA_EXCEEDED = "quota_exceeded";
export const RATE_LIMITED = "rate_limited";
export const DEFAULT_PREFLIGHT_TIMEOUT_MS = 60_000;
export const BOOTSTRAP_TOKEN_REQUIRED = "bootstrap_token_required";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function requireBootstrapToken(value) {
  const token = String(value ?? "").trim();
  if (!token) {
    const error = new Error("Doubao readiness requires AI Team bootstrap credentials");
    error.code = BOOTSTRAP_TOKEN_REQUIRED;
    throw error;
  }
  return token;
}

export function requireProviderCredential(value) {
  const token = String(value ?? "").trim();
  if (!token) {
    const error = new Error("Doubao API credential is missing: set DOUBAO_API_KEY, ARK_API_KEY, or DOUBAO_API_KEY_FILE");
    error.code = CREDENTIAL_UNAVAILABLE;
    throw error;
  }
  return token;
}

export function isReadinessResponse(value) {
  return String(value ?? "").trim() === READINESS_TOKEN;
}

function readinessReason(error) {
  if (error?.code === CREDENTIAL_UNAVAILABLE) return CREDENTIAL_UNAVAILABLE;
  if (error?.code === QUOTA_EXCEEDED) return QUOTA_EXCEEDED;
  if (error?.code === RATE_LIMITED) return RATE_LIMITED;
  return MODEL_UNAVAILABLE;
}

export function isRetryableReadinessError(error) {
  // 凭据缺失或已触发账户额度限制，不能无限调用可能计费的接口。
  return ![CREDENTIAL_UNAVAILABLE, QUOTA_EXCEEDED, BOOTSTRAP_TOKEN_REQUIRED]
    .includes(error?.code);
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
      await client.reportReadiness({ agentId: "doubao", modelAvailable: true, bootstrapToken: token });
      return { ready: true, attempts };
    } catch (error) {
      const reason = readinessReason(error);
      await client.reportReadiness({ agentId: "doubao", modelAvailable: false, reason, bootstrapToken: token }).catch(() => {});
      // 不输出服务端原始响应，以免其中包含 account/token 信息。
      logger?.error?.(`Doubao readiness: ${reason}`);
      if (!isRetryableReadinessError(error) || attempts >= maxAttempts) {
        return { ready: false, attempts, error, reason };
      }
      await sleepImpl(retryIntervalMs);
    }
  }
  return { ready: false, attempts };
}

export async function startDoubaoWorker({ client, workerFactory, preflight, bootstrapToken, retryIntervalMs = 30_000, maxAttempts = Infinity, sleepImpl = sleep } = {}) {
  requireBootstrapToken(bootstrapToken);
  const readiness = await waitForModel({ client, preflight, bootstrapToken, retryIntervalMs, maxAttempts, sleepImpl });
  if (!readiness.ready) return { started: false, readiness };
  if (typeof workerFactory !== "function") throw new TypeError("workerFactory is required");
  const worker = workerFactory();
  await worker.start();
  return { started: true, worker, readiness };
}
