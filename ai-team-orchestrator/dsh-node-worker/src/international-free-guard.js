// DSH 国际站免费模型硬性接单门禁。
// 只读取本地国际站 App 插件目录及状态；绝不读取/输出账号 token，
// 不调用模型，不允许回退到国内站、SG 付费模型或 DSH 官方 API。
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const execFileAsync = promisify(execFile);
export const PROVIDER = "workbuddy-ai";
export const MODEL = "deepseek-v4.1-flash";
export const MODEL_LABEL = "WorkBuddy AI international / DeepSeek V4.1 Flash";
export const MAX_CATALOG_AGE_MS = 24 * 60 * 60 * 1000;
export const ISOLATED_PROFILE = "ai-team-intl";
// 0.7.6 修复了国际站对 X-Conversation-* 三个请求头的强制校验。
// 禁止自动升到 0.7.7/0.7.8；当前隔离内核仍固定 0.2.0-rc.2。
export const REQUIRED_PLUGIN_VERSION = "0.7.6";
const MIN_PROMO_WINDOW_MS = 10 * 60 * 1000;

const fail = (reason) => {
  const error = new Error("DSH international free model unavailable: " + reason);
  error.code = "international_free_gate";
  error.reason = reason;
  return error;
};

/** 必须同时是国际版账号、已登录、使用桌面国际版来源。 */
export function checkAccount(data) {
  if (data?.provider !== PROVIDER) throw fail("provider_not_international");
  if (data?.domain !== "www.workbuddy.ai") throw fail("domain_not_international");
  if (data?.status !== "signed-in") throw fail("international_account_not_signed_in");
  if (data?.source !== "desktop") throw fail("not_international_desktop_login");
  return true;
}

/** 最后生效配置必须精确匹配国际站非 SG 的目标模型。 */
export function checkEffectiveConfig(config) {
  const lines = String(config).split(/\r?\n/);
  const defaults = [];
  let pluginCount = 0;
  for (let i = 0; i < lines.length; i += 1) {
    if (/^- id: agent-default-model\s*$/.test(lines[i])) {
      const block = [];
      for (let j = i + 1; j < lines.length && !/^- (?:id|insert): /.test(lines[j]); j += 1) {
        block.push(lines[j]);
      }
      const text = block.join("\n");
      const provider = text.match(/^\s{4}provider: ([^\n]+)$/m)?.[1]?.trim();
      const model = text.match(/^\s{4}model: ([^\n]+)$/m)?.[1]?.trim();
      defaults.push({ provider, model });
    }
    if (/^\s+name: dsh-workbuddy-connect\s*$/.test(lines[i])) pluginCount += 1;
  }
  if (defaults.length !== 1 || defaults[0].provider !== PROVIDER ||
      defaults[0].model !== MODEL) throw fail("default_model_not_exactly_international_free");
  if (pluginCount !== 1) throw fail("international_plugin_not_loaded_once");
  return true;
}

/** 缓存只接受来源明确且较新的国际站 App 目录，不接受内置静态价目表。 */
export function checkInternationalCatalog(snapshot, now = Date.now()) {
  const entries = snapshot?.entries;
  if (!entries || typeof entries !== "object" || Array.isArray(entries) ||
      Object.keys(entries).length !== 1) throw fail("catalog_missing_or_account_ambiguous");
  const entry = Object.values(entries)[0];
  if (entry?.source !== "workbuddy-ai:app") throw fail("catalog_not_from_international_app");
  const fetched = entry.fetchedAtMs;
  if (typeof fetched !== "number" || now < fetched - 120_000 ||
      now - fetched > MAX_CATALOG_AGE_MS) throw fail("international_catalog_stale");
  if (!Array.isArray(entry.models)) throw fail("international_models_missing");
  const matches = entry.models.filter((item) => item?.id === MODEL);
  if (matches.length !== 1) throw fail("allowed_model_missing_or_ambiguous");
  const model = matches[0];
  if (model?.billing?.free !== true || model?.billing?.credits !== "x0.00") {
    throw fail("allowed_model_is_not_free");
  }
  const promotions = model.promotions;
  if (!Array.isArray(promotions) || !promotions.some((promo) =>
    Number.isFinite(promo?.start) && Number.isFinite(promo?.end) &&
    promo?.factor === 0 && promo.start <= now &&
    promo.end >= now + MIN_PROMO_WINDOW_MS)) {
    throw fail("free_promotion_expired_or_unverified");
  }
  return { provider: PROVIDER, model: MODEL, source: entry.source, fetchedAtMs: fetched };
}

export async function assertInternationalFreeEligible({
  now = Date.now(), runCommand = execFileAsync, home = homedir(),
} = {}) {
  const dshHome = process.env.DSH_HOME || join(home, ".dsh");
  const nodeBin = process.env.DSH_NODE_BIN ||
    join(home, ".local/opt/nodejs/v22.22.2/bin/node");
  const dshBin = process.env.DSH_BIN ||
    join(home, ".local/state/ai-team/dsh-intl-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js");
  if (process.env.DSH_PROFILE && process.env.DSH_PROFILE !== ISOLATED_PROFILE) {
    throw fail("wrong_dsh_profile");
  }
  const pluginBin = join(dshHome,
    "profiles", ISOLATED_PROFILE, "node_modules/dsh-workbuddy-connect/lib/bin.js");
  const snapshot = JSON.parse(await readFile(
    join(dshHome, ".workbuddy-ai-catalog.json"), "utf8"));
  const proof = checkInternationalCatalog(snapshot, now);
  const pluginManifest = JSON.parse(await readFile(
    join(dshHome, "profiles", ISOLATED_PROFILE, "node_modules/dsh-workbuddy-connect/package.json"),
    "utf8"));
  if (pluginManifest.version !== REQUIRED_PLUGIN_VERSION) {
    throw fail("incorrect_isolated_plugin_version");
  }
  const coreManifest = JSON.parse(await readFile(
    join(home, ".local/state/ai-team/dsh-intl-runtime/node_modules/@deepseek-ai/dsh/package.json"),
    "utf8"));
  if (coreManifest.version !== "0.2.0-rc.2") throw fail("incorrect_isolated_core_version");
  const opts = { timeout: 18_000, maxBuffer: 240_000, encoding: "utf8" };
  const [auth, composed] = await Promise.all([
    runCommand(nodeBin, [pluginBin, "status", "--provider", PROVIDER, "--json"], opts),
    runCommand(nodeBin, [dshBin, "--profile", ISOLATED_PROFILE, "--dump-config"], opts),
  ]);
  checkAccount(JSON.parse(auth.stdout));
  checkEffectiveConfig(composed.stdout);
  return proof;
}
