import test from "node:test";
import assert from "node:assert/strict";
import { DSH_FREE_MODEL, resolveDshModelBackend } from "../src/index.js";
import {
  MODEL, PROVIDER, checkAccount, checkEffectiveConfig,
  checkInternationalCatalog, MAX_CATALOG_AGE_MS, REQUIRED_PLUGIN_VERSION,
} from "../src/international-free-guard.js";

const now = Date.UTC(2026, 9, 12);
const snapshot = () => ({
  version: 1, entries: {
    "opaque-cache-identifier": {
      source: "workbuddy-ai:app", fetchedAtMs: now - 3_600_000,
      models: [
        { id: "deepseek-v4.1-flash-sg", billing: { credits: "x0.03", free: false } },
        { id: MODEL, billing: { credits: "x0.00", free: true },
          promotions: [{ factor: 0, start: now - 3_600_000, end: now + 3_600_000 }] },
      ],
    },
  },
});
const config = [
  "- id: agent-default-model", "  name: '@deepseek-ai/dsh-agent-default-model'",
  "  config:", "    provider: workbuddy-ai", "    model: deepseek-v4.1-flash",
  "- insert:", "    - id: llm-workbuddy", "      name: dsh-workbuddy-connect",
].join("\n");

test("默认只能用国际站插件，老的中国 CodeBuddy 后端禁止使用", () => {
  assert.equal(REQUIRED_PLUGIN_VERSION, "0.7.6");
  assert.equal(resolveDshModelBackend({}), "international_plugin");
  assert.equal(resolveDshModelBackend({ DSH_MODEL_BACKEND: "international_plugin" }),
    "international_plugin");
  assert.equal(resolveDshModelBackend({ DSH_MODEL_BACKEND: "native" }), "native");
  assert.throws(() => resolveDshModelBackend({ DSH_MODEL_BACKEND: "workbuddy" }));
  assert.equal(DSH_FREE_MODEL, "deepseek-v4.1-flash");
  assert.equal(PROVIDER, "workbuddy-ai");
});

test("账号必须属于国际版 WorkBuddy AI，不能复用中国站状态", () => {
  const valid = { provider: "workbuddy-ai", domain: "www.workbuddy.ai",
                  status: "signed-in", source: "desktop" };
  assert.equal(checkAccount(valid), true);
  for (const altered of [
    { provider: "workbuddy" }, { domain: "copilot.tencent.com" },
    { status: "logged-out" }, { source: "cli" },
  ]) assert.throws(() => checkAccount({ ...valid, ...altered }), /international free model unavailable/);
});

test("真实配置必须精确绑定国际非 SG 免费模型和单个插件", () => {
  assert.equal(checkEffectiveConfig(config), true);
  for (const invalid of [
    config.replace("provider: workbuddy-ai", "provider: workbuddy"),
    config.replace("model: deepseek-v4.1-flash", "model: deepseek-v4.1-flash-sg"),
    config.replace("name: dsh-workbuddy-connect", "name: other-plugin"),
    config + "\n" + config,
  ]) assert.throws(() => checkEffectiveConfig(invalid), /international free model unavailable/);
});

test("过期目录、促销结束、价格变化、账号不唯一时必须拒绝模型调用", () => {
  assert.equal(checkInternationalCatalog(snapshot(), now).model, MODEL);
  const expired = snapshot(); expired.entries["opaque-cache-identifier"].models[1].promotions[0].end = now;
  assert.throws(() => checkInternationalCatalog(expired, now), /free_promotion_expired/);
  const charged = snapshot(); charged.entries["opaque-cache-identifier"].models[1].billing.free = false;
  assert.throws(() => checkInternationalCatalog(charged, now), /allowed_model_is_not_free/);
  const otherPrice = snapshot(); otherPrice.entries["opaque-cache-identifier"].models[1].billing.credits = "x0.03";
  assert.throws(() => checkInternationalCatalog(otherPrice, now), /allowed_model_is_not_free/);
  const stale = snapshot(); stale.entries["opaque-cache-identifier"].fetchedAtMs = now - MAX_CATALOG_AGE_MS - 1;
  assert.throws(() => checkInternationalCatalog(stale, now), /international_catalog_stale/);
  const embedded = snapshot(); embedded.entries["opaque-cache-identifier"].source = "builtin";
  assert.throws(() => checkInternationalCatalog(embedded, now), /catalog_not_from_international_app/);
  const ambiguous = snapshot(); ambiguous.entries.second = snapshot().entries["opaque-cache-identifier"];
  assert.throws(() => checkInternationalCatalog(ambiguous, now), /account_ambiguous/);
});
