/**
 * OpenCode 多免费模型的离线价格核验。
 * 只读取 OpenCode 已在本机缓存的 provider 模型元数据；禁止联网刷新、
 * 自动降级到未知模型，或把名字包含 free 当成价格证据。
 */
import { readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

export const PROVIDER = "opencode";
export const DEFAULT_MODEL = "opencode/mimo-v2.6-flash-free";
export const CATALOG_PATH = join(homedir(), ".cache", "opencode", "models.json");
export const MAX_CACHE_AGE_MS = 60 * 60_000;
// 必须由维护者逐项审查；其他提供商同名模型或动态候选项不自动接入。
export const FREE_MODEL_ROSTER = Object.freeze({
  "opencode/mimo-v2.6-flash-free": "MiMo 2.6 Flash / 中文分析",
  "opencode/deepseek-v4.1-flash-free": "DeepSeek V4.1 Flash / 代码审查",
  "opencode/glm-5.3-flash-free": "GLM 5.3 Flash / 综合审核",
  "opencode/nemotron-3.5-lightning-free": "Nemotron 3.5 Lightning / 独立核查",
  "opencode/longcat-2.5-preview-free": "LongCat 2.5 / 备选审查",
  "opencode/space-bunny-free": "Space Bunny / 备选审查",
  "opencode/big-pickle": "Big Pickle / 仅当实时缓存价格严格为零",
});

export function requireAllowlistedModel(model) {
  if (typeof model !== "string" || !Object.hasOwn(FREE_MODEL_ROSTER, model)) {
    throw new Error("opencode_model_not_allowlisted");
  }
  return model;
}

/** 目录的新鲜度与价格必须同时成立，未知价格、缓存计费与弃用均不放行。 */
export function checkFreeModelPrice(model, catalog, { now = Date.now(), modifiedMs } = {}) {
  requireAllowlistedModel(model);
  if (!Number.isFinite(modifiedMs) || modifiedMs > now + 120_000 ||
      now - modifiedMs > MAX_CACHE_AGE_MS) {
    throw new Error("opencode_catalog_stale");
  }
  const id = model.slice(PROVIDER.length + 1);
  const entry = catalog?.[PROVIDER]?.models?.[id];
  if (!entry || entry.id !== id || entry.status === "deprecated" ||
      entry.status === "disabled" || entry.status === "unavailable") {
    throw new Error("opencode_free_model_missing_or_inactive");
  }
  const cost = entry.cost;
  if (!cost || cost.input !== 0 || cost.output !== 0 ||
      ["cache_read", "cache_write"].some((key) =>
        Object.hasOwn(cost, key) && cost[key] !== 0)) {
    throw new Error("opencode_model_not_verified_free");
  }
  return { model, provider: PROVIDER, priceVerified: true, checkedAtMs: now,
           cacheAgeMs: Math.max(0, now - modifiedMs) };
}

export async function requireCurrentFreeModel(model, {
  cachePath = CATALOG_PATH, now = Date.now, fileReader = readFile, statReader = stat,
} = {}) {
  requireAllowlistedModel(model);
  try {
    const [content, metadata] = await Promise.all([
      fileReader(cachePath, "utf8"), statReader(cachePath),
    ]);
    if (!metadata.isFile()) throw new Error("opencode_catalog_invalid_file");
    return checkFreeModelPrice(model, JSON.parse(content), {
      now: now(), modifiedMs: metadata.mtimeMs,
    });
  } catch (error) {
    // 只保留枚举型错误，不显示本机配置路径、token 或模型服务原始返回。
    if (String(error?.message || "").startsWith("opencode_")) throw error;
    throw new Error("opencode_free_catalog_unavailable");
  }
}

/** 由可信的任务元数据选择模型；不接受任务正文内的自动模型切换指令。 */
export function resolveTaskModel(task, { env = process.env } = {}) {
  const selected = task?.metadata?.opencode_model || env.OPENCODE_MODEL || DEFAULT_MODEL;
  return requireAllowlistedModel(selected);
}
