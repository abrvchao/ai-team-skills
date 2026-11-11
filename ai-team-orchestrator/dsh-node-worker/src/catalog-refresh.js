/**
 * WorkBuddy AI 国际版免费模型目录：显式授权、单次刷新、离线可测。
 *
 * 基于 Muse 的 catalog-refresh 设计集成，但严格使用本机真实插件的数据结构。
 * 默认不访问外部网络、不发 prompt、不刷新 token、不启用任何 worker。
 * 只有人工明确设置 DSH_CATALOG_REFRESH_APPROVED_ONCE=1 才允许执行目录请求。
 */
import { promises as fs } from "node:fs";
import { randomUUID } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  checkInternationalCatalog, ISOLATED_PROFILE, MODEL,
} from "./international-free-guard.js";

export const CATALOG_CACHE_PATH = join(homedir(), ".dsh", ".workbuddy-ai-catalog.json");
export const REFRESH_OK = "refreshed_eligible";
export const REFRESH_NOT_FREE = "refreshed_not_free";
export const REFRESH_FAILED = "refresh_blocked";
export const REFRESH_SKIPPED = "refresh_not_authorized";
export const ONE_SHOT_APPROVAL = "DSH_CATALOG_REFRESH_APPROVED_ONCE";

/**
 * 加载国际版 WorkBuddy 5.6 加密凭据必须指定它自己的 Electron 二进制。
 * 默认插件 CredentialStore(discovery=none)，即使 App 已安装也不会自动搜索。
 * 只在 macOS 填写 WorkBuddy AI 标准路径，不用 CN 版本，不启动模型。
 */
export function ensureInternationalElectronHelper({
  env = process.env, platform = process.platform,
} = {}) {
  if (platform !== "darwin" || env.WORKBUDDY_AI_ELECTRON_BIN) return false;
  env.WORKBUDDY_AI_ELECTRON_BIN =
    "/Applications/WorkBuddy AI.app/Contents/MacOS/Electron";
  return true;
}

/** 官方插件自己解析 /v3/config 的国际版模型、促销和 App 身份。
 * 绝不在这里用无 App User-Agent 的普通 GET 冒充官方目录。
 * 此函数只有被显式授权的 refreshCatalog 调用时才会执行。
 */
export async function fetchFromInstalledPlugin({
  home = homedir(), now = Date.now, loadPlugin,
} = {}) {
  ensureInternationalElectronHelper();
  const modulePath = join(home, ".dsh", "profiles", ISOLATED_PROFILE,
                          "node_modules", "dsh-workbuddy-connect", "lib", "index.js");
  const plugin = loadPlugin
    ? await loadPlugin()
    : await import(pathToFileURL(modulePath).href);
  const store = new plugin.WorkBuddyCredentialStore({
    variant: plugin.AI_VARIANT,
    // 只允许现有有效凭据；不自动调用登录/refresh token 远端接口。
    refresh: async () => { throw new Error("credential_refresh_forbidden"); },
  });
  const credential = await store.current();
  if (!credential || credential.domain !== "www.workbuddy.ai" ||
      typeof credential.uid !== "string" || !credential.uid ||
      !Number.isFinite(credential.expiresAtMs) ||
      credential.expiresAtMs < now() + 60_000) {
    throw new Error("international_credential_missing_or_expiring");
  }
  // 插件内部 credentialIdentity() 未导出；保持与它相同的账号键定义。
  const account = `${credential.uid}:${credential.enterpriseId ?? ""}`;
  if (typeof account !== "string" || !account.trim()) {
    throw new Error("international_identity_invalid");
  }
  const client = new plugin.WorkBuddyUpstreamClient();
  // 只调用官方插件的 fetchModels() => GET /v3/config；绝不调用 chatStream。
  const models = await client.fetchModels(credential);
  const metadata = client.lastCatalog;
  if (metadata?.source !== "workbuddy-ai:app" ||
      !Number.isFinite(metadata.fetchedAtMs) || !Array.isArray(models)) {
    throw new Error("international_catalog_origin_invalid");
  }
  return {
    account, source: metadata.source, fetchedAtMs: metadata.fetchedAtMs,
    models, ...(metadata.appVersion?.version
      ? { appVersion: metadata.appVersion.version } : {}),
  };
}

/** 用原安全门禁直接核验完整目录，不自己另写宽松价格规则。 */
export function checkModelFreeEligible(snapshot, now = Date.now()) {
  try {
    return { eligible: true, proof: checkInternationalCatalog(snapshot, now), reason: "ok" };
  } catch (error) {
    return { eligible: false, reason: error?.reason || "invalid_catalog" };
  }
}

/** 只有经过门禁验证后才原子写入缓存；失败不得覆盖已有的良好目录。 */
export async function writeSnapshotAtomic(filePath, snapshot) {
  const tmp = `${filePath}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(tmp, `${JSON.stringify(snapshot, null, 2)}\n`,
                       { mode: 0o600, flag: "wx" });
    await fs.rename(tmp, filePath);
  } finally {
    await fs.rm(tmp, { force: true }).catch(() => {});
  }
}

/**
 * 只在 approved === true 时发出一次目录请求。
 * fetcher/save 可注入以完全离线测试；默认 fetcher 只使用 WorkBuddy AI 官方插件。
 */
export async function refreshCatalog({
  approved = false, fetcher = fetchFromInstalledPlugin, cachePath = CATALOG_CACHE_PATH,
  now = Date.now, save = writeSnapshotAtomic,
} = {}) {
  if (approved !== true) {
    return { status: REFRESH_SKIPPED, reason: "explicit_approval_required" };
  }
  let data;
  try {
    data = await fetcher({ now });
  } catch {
    // 不返回原始异常文本，里面可能有身份或 token 相关信息。
    return { status: REFRESH_FAILED, reason: "upstream_catalog_unavailable" };
  }
  // 必须是唯一账号、国际 App 目录、可信时间和明确的计费/促销窗口。
  if (!data || typeof data.account !== "string" || !data.account.trim() ||
      data.account.length > 200 || data.source !== "workbuddy-ai:app" ||
      !Array.isArray(data.models) || !Number.isFinite(data.fetchedAtMs)) {
    return { status: REFRESH_FAILED, reason: "catalog_origin_or_shape_invalid" };
  }
  const candidate = {
    version: 1,
    entries: {
      [data.account]: {
        account: data.account, source: data.source,
        fetchedAtMs: data.fetchedAtMs, models: data.models,
        ...(typeof data.appVersion === "string" ? { appVersion: data.appVersion } : {}),
      },
    },
  };
  const verdict = checkModelFreeEligible(candidate, now());
  if (!verdict.eligible) {
    const notFree = ["allowed_model_is_not_free", "free_promotion_expired_or_unverified",
                     "allowed_model_missing_or_ambiguous"].includes(verdict.reason);
    return { status: notFree ? REFRESH_NOT_FREE : REFRESH_FAILED, reason: verdict.reason };
  }
  try {
    await save(cachePath, candidate);
  } catch {
    return { status: REFRESH_FAILED, reason: "cache_write_failed" };
  }
  return { status: REFRESH_OK, reason: "verified_free", fetchedAtMs: data.fetchedAtMs };
}

/** cache 新鲜必须同时满足真实的免费门禁，而非仅检查日期。 */
export async function isCacheFresh({
  cachePath = CATALOG_CACHE_PATH, now = Date.now, readFile = fs.readFile,
} = {}) {
  try {
    const snapshot = JSON.parse(await readFile(cachePath, "utf8"));
    const verdict = checkModelFreeEligible(snapshot, now());
    return { fresh: verdict.eligible, reason: verdict.reason };
  } catch {
    return { fresh: false, reason: "cache_missing_or_invalid" };
  }
}

/**
 * Worker 启动前调用；除非本次进程明确设置一次性批准变量，否则直接返回。
 * 启动脚本默认不设置此变量，因此不会意外触发第三方 API。
 */
export async function refreshBeforePreflightIfApproved({
  env = process.env, inspect = isCacheFresh, refresh = refreshCatalog,
} = {}) {
  if (env[ONE_SHOT_APPROVAL] !== "1") {
    return { status: REFRESH_SKIPPED, reason: "explicit_approval_required" };
  }
  const cached = await inspect();
  if (cached.fresh) return { status: "existing_catalog_eligible" };
  const result = await refresh({ approved: true });
  if (result.status !== REFRESH_OK) {
    const error = new Error(`DSH catalog refresh blocked: ${result.reason}`);
    error.code = "international_catalog_refresh_blocked";
    throw error;
  }
  return result;
}
