/** Muse 目录刷新方案的本机安全集成测试：完全离线、零 prompt、零外部请求。 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  CATALOG_CACHE_PATH, REFRESH_OK, REFRESH_NOT_FREE, REFRESH_FAILED, REFRESH_SKIPPED,
  ONE_SHOT_APPROVAL, checkModelFreeEligible, refreshCatalog,
  isCacheFresh, refreshBeforePreflightIfApproved, fetchFromInstalledPlugin,
  ensureInternationalElectronHelper,
} from "../src/catalog-refresh.js";

const NOW = Date.UTC(2026, 9, 17, 12);
const MODEL = "deepseek-v4.1-flash";
const model = (billing = { free: true, credits: "x0.00" }, promotions = [
  { start: NOW - 100_000, end: NOW + 3_600_000, factor: 0 },
]) => ({ id: MODEL, name: "Synthetic test model", billing, promotions });
const data = (changes = {}) => ({
  account: "synthetic-account", source: "workbuddy-ai:app",
  fetchedAtMs: NOW - 1_000, models: [model()], ...changes,
});
const snapshot = (record = data()) => ({
  version: 1, entries: { [record.account]: { ...record } },
});
const run = (record = data(), changes = {}) => {
  let calls = 0;
  let saved = null;
  return {
    calls: () => calls, saved: () => saved,
    execute: () => refreshCatalog({
      approved: true, now: () => NOW, cachePath: "/synthetic/cache.json",
      fetcher: async () => { calls++; return record; },
      save: async (file, contents) => { saved = { file, contents }; },
      ...changes,
    }),
  };
};

test("默认没有用户批准时绝不调用 fetcher，也不写入缓存", async () => {
  let touched = false;
  const result = await refreshCatalog({
    fetcher: async () => { touched = true; throw new Error("must not call"); },
    save: async () => { touched = true; },
  });
  assert.equal(result.status, REFRESH_SKIPPED);
  assert.equal(touched, false);
  assert.ok(CATALOG_CACHE_PATH.endsWith(".workbuddy-ai-catalog.json"));
});

test("仅匹配原有国际安全门禁的目录格式才允许成功保存", async () => {
  const x = run();
  const result = await x.execute();
  assert.equal(result.status, REFRESH_OK);
  assert.equal(x.calls(), 1);
  const actual = x.saved().contents;
  assert.equal(actual.version, 1);
  assert.deepEqual(Object.keys(actual.entries), ["synthetic-account"]);
  assert.equal(checkModelFreeEligible(actual, NOW).eligible, true);
});

test("free=false 或付费价格直接拒绝，旧缓存绝不覆盖", async () => {
  for (const billing of [
    { free: false, credits: "x0.00" },
    { free: true, credits: "x0.03" },
    { free: true, credits: "" },
    { free: true }, // 不允许 Muse 旧版逻辑的空价格等于免费
    { free: true, credits: 0 },
  ]) {
    const x = run(data({ models: [model(billing)] }));
    const out = await x.execute();
    assert.equal(out.status, REFRESH_NOT_FREE, JSON.stringify(billing));
    assert.equal(x.saved(), null);
  }
});

test("没有有效促销、促销已结束或即将结束时拒绝", async () => {
  for (const promos of [[], [
    { start: NOW - 1_000, end: NOW - 1, factor: 0 },
  ], [
    { start: NOW - 1_000, end: NOW + 1000, factor: 0 },
  ], [
    { start: NOW - 1_000, end: NOW + 3_600_000, factor: 0.5 },
  ]]) {
    const x = run(data({ models: [model(undefined, promos)] }));
    assert.equal((await x.execute()).status, REFRESH_NOT_FREE);
    assert.equal(x.saved(), null);
  }
});

test("其他供应商来源、过期目录、未来时钟和错误结构一律拒绝", async () => {
  const wrong = [
    data({ source: "workbuddy:cli" }),
    data({ source: "fallback" }),
    data({ fetchedAtMs: NOW - 25 * 60 * 60 * 1000 }),
    data({ fetchedAtMs: NOW + 180_000 }),
    data({ account: "" }),
    data({ models: "not-an-array" }),
    null,
  ];
  for (const input of wrong) {
    const x = run(input);
    assert.equal((await x.execute()).status, REFRESH_FAILED, JSON.stringify(input)?.slice(0, 80));
    assert.equal(x.saved(), null);
  }
});

test("同 ID 模型不唯一、不含目标模型必须拒绝", async () => {
  for (const models of [[model(), model()], [{ id: "paid-sg", billing: { free: true, credits: "x0.00" } }]]) {
    const x = run(data({ models }));
    assert.equal((await x.execute()).status, REFRESH_NOT_FREE);
    assert.equal(x.saved(), null);
  }
});

test("上游离线时拒绝，错误不会泄漏原始 token 异常", async () => {
  let saved = false;
  const result = await refreshCatalog({
    approved: true,
    fetcher: async () => { throw new Error("Bearer synthetic-secret"); },
    save: async () => { saved = true; },
  });
  assert.equal(result.status, REFRESH_FAILED);
  assert.ok(!JSON.stringify(result).includes("synthetic-secret"));
  assert.equal(saved, false);
});

test("缓存写入失败不会假报恢复", async () => {
  const x = run(data(), { save: async () => { throw new Error("disk-full"); } });
  assert.equal((await x.execute()).status, REFRESH_FAILED);
});

test("isCacheFresh 需要来源/价格/促销/时间全部通过", async () => {
  const valid = snapshot();
  const yes = await isCacheFresh({ now: () => NOW, readFile: async () => JSON.stringify(valid) });
  assert.equal(yes.fresh, true);
  for (const altered of [
    snapshot(data({ fetchedAtMs: NOW - 25 * 60 * 60 * 1000 })),
    snapshot(data({ source: "other-app" })),
    snapshot(data({ models: [model({ free: true, credits: "" })] })),
  ]) {
    const value = await isCacheFresh({ now: () => NOW, readFile: async () => JSON.stringify(altered) });
    assert.equal(value.fresh, false);
  }
  assert.equal((await isCacheFresh({ readFile: async () => { throw Error("missing"); } })).fresh, false);
});

test("启动默认不检查缓存、不请求 API、不进行自动刷新", async () => {
  let calls = 0;
  const out = await refreshBeforePreflightIfApproved({
    env: {}, inspect: async () => { calls++; }, refresh: async () => { calls++; },
  });
  assert.equal(out.status, REFRESH_SKIPPED);
  assert.equal(calls, 0);
});

test("一次性批准后，已有合格目录则跳过远端刷新", async () => {
  let calls = 0;
  const out = await refreshBeforePreflightIfApproved({
    env: { [ONE_SHOT_APPROVAL]: "1" },
    inspect: async () => ({ fresh: true }),
    refresh: async () => { calls++; },
  });
  assert.equal(out.status, "existing_catalog_eligible");
  assert.equal(calls, 0);
});

test("明确批准+本地过期时只请求一次，成功后继续原门禁", async () => {
  let calls = 0;
  const out = await refreshBeforePreflightIfApproved({
    env: { [ONE_SHOT_APPROVAL]: "1" },
    inspect: async () => ({ fresh: false }),
    refresh: async ({ approved }) => {
      assert.equal(approved, true);
      calls++;
      return { status: REFRESH_OK, fetchedAtMs: NOW };
    },
  });
  assert.equal(out.status, REFRESH_OK);
  assert.equal(calls, 1);
});

test("批准后仍失败必须抛出阻塞，不能放行启动", async () => {
  await assert.rejects(refreshBeforePreflightIfApproved({
    env: { [ONE_SHOT_APPROVAL]: "1" },
    inspect: async () => ({ fresh: false }),
    refresh: async () => ({ status: REFRESH_FAILED, reason: "no-network" }),
  }), /catalog refresh blocked/);
});

/** 通过完全离线的假插件，验证实际适配器依赖的公开 API（不读取本机凭据）。 */
function fakeInstalledPlugin(credential, events = []) {
  return {
    AI_VARIANT: { id: "workbuddy-ai" },
    WorkBuddyCredentialStore: class {
      constructor(options) { assert.equal(options.variant.id, "workbuddy-ai"); }
      async current() { events.push("read_local_credentials"); return credential; }
    },
    WorkBuddyUpstreamClient: class {
      async fetchModels() {
        events.push("fetch_catalog_only");
        this.lastCatalog = { source: "workbuddy-ai:app", fetchedAtMs: NOW };
        return [model()];
      }
    },
  };
}

test("适配器从隔离账号读取目录，使用插件规范化结构，不需要未导出的函数", async () => {
  const events = [];
  const credential = {
    uid: "synthetic-uid", enterpriseId: undefined,
    domain: "www.workbuddy.ai", expiresAtMs: NOW + 60_000 * 30,
  };
  const result = await fetchFromInstalledPlugin({
    now: () => NOW, loadPlugin: async () => fakeInstalledPlugin(credential, events),
  });
  assert.equal(result.account, "synthetic-uid:");
  assert.equal(result.source, "workbuddy-ai:app");
  assert.equal(result.models[0].billing.credits, "x0.00");
  assert.deepEqual(events, ["read_local_credentials", "fetch_catalog_only"]);
});

test("适配器拒绝国内账号、过期登录或空账号，且不触发目录请求", async () => {
  for (const credential of [
    { uid: "x", domain: "copilot.tencent.com", expiresAtMs: NOW + 999_999 },
    { uid: "x", domain: "www.workbuddy.ai", expiresAtMs: NOW },
    { uid: "", domain: "www.workbuddy.ai", expiresAtMs: NOW + 999_999 },
  ]) {
    const events = [];
    await assert.rejects(fetchFromInstalledPlugin({
      now: () => NOW, loadPlugin: async () => fakeInstalledPlugin(credential, events),
    }), /credential_missing_or_expiring/);
    assert.deepEqual(events, ["read_local_credentials"]);
  }
});

test("macOS 国际版加密凭据助手只绑定 AI App，不覆盖显式配置", () => {
  const env = {};
  assert.equal(ensureInternationalElectronHelper({ env, platform: "darwin" }), true);
  assert.equal(env.WORKBUDDY_AI_ELECTRON_BIN,
               "/Applications/WorkBuddy AI.app/Contents/MacOS/Electron");
  assert.equal(ensureInternationalElectronHelper({ env, platform: "darwin" }), false);
  const linux = {};
  assert.equal(ensureInternationalElectronHelper({ env: linux, platform: "linux" }), false);
  assert.equal(linux.WORKBUDDY_AI_ELECTRON_BIN, undefined);
  const custom = { WORKBUDDY_AI_ELECTRON_BIN: "/custom/WorkBuddyAI" };
  assert.equal(ensureInternationalElectronHelper({ env: custom, platform: "darwin" }), false);
  assert.equal(custom.WORKBUDDY_AI_ELECTRON_BIN, "/custom/WorkBuddyAI");
});
