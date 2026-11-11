// 免费模型池：所有测试只用合成元数据，绝不联网或调用模型。
import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_MODEL, FREE_MODEL_ROSTER, MAX_CACHE_AGE_MS,
  checkFreeModelPrice, requireCurrentFreeModel, resolveTaskModel,
} from "../src/free-model-gate.js";
import { buildArgs, runOpenCode, handleTask } from "../src/index.js";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

const NOW = Date.UTC(2026, 9, 18);
const DEFAULT_ID = DEFAULT_MODEL.slice("opencode/".length);
const model = (cost = { input: 0, output: 0, cache_read: 0 }) => ({
  id: DEFAULT_ID, cost,
});
const catalog = (item = model()) => ({ opencode: { models: { [DEFAULT_ID]: item } } });
const verify = (item = model(), modifiedMs = NOW - 1000) =>
  checkFreeModelPrice(DEFAULT_MODEL, catalog(item), { now: NOW, modifiedMs });

test("同一 Worker 只接受维护者审查的多个确切 OpenCode 免费模型 ID", () => {
  assert.ok(Object.keys(FREE_MODEL_ROSTER).length >= 5);
  assert.equal(resolveTaskModel({ metadata: { opencode_model: "opencode/glm-5.3-flash-free" } }, { env: {} }),
               "opencode/glm-5.3-flash-free");
  assert.equal(resolveTaskModel({}, { env: {} }), DEFAULT_MODEL);
  for (const forbidden of ["opencode/claude-sonnet-4.6", "openrouter/glm-5.3-flash-free",
                           "opencode/exo-free", "opencode/mimo-v2.6-flash-free --auto"] ) {
    assert.throws(() => resolveTaskModel({ metadata: { opencode_model: forbidden } }), /not_allowlisted/);
  }
  const args = buildArgs("Synthetic review", { model: "opencode/glm-5.3-flash-free" });
  assert.equal(args[args.indexOf("--model") + 1], "opencode/glm-5.3-flash-free");
  assert.ok(args.includes("--pure") && args.includes("plan"));
  assert.ok(args.includes("--title"));
  assert.ok(!args.includes("--auto"));
});

test("必须同时零输入、零输出、零缓存费用且当前可用", () => {
  assert.equal(verify().priceVerified, true);
  for (const cost of [
    { input: 0.00001, output: 0 }, { input: 0, output: 1 },
    { input: 0, output: 0, cache_read: 1 }, { input: 0, output: 0, cache_write: 1 },
    { input: "0", output: 0 }, { output: 0 }, {}, null,
  ]) {
    assert.throws(() => verify(model(cost)), /not_verified_free/);
  }
  assert.throws(() => verify({ ...model(), status: "deprecated" }), /missing_or_inactive/);
  assert.throws(() => verify({ ...model(), id: "another-model" }), /missing_or_inactive/);
  assert.throws(() => checkFreeModelPrice(DEFAULT_MODEL, { openrouter: catalog() },
                                              { now: NOW, modifiedMs: NOW }), /missing_or_inactive/);
});

test("过期缓存、未来目录、缺目录一律 fail-closed", () => {
  assert.throws(() => verify(model(), NOW - MAX_CACHE_AGE_MS - 1), /catalog_stale/);
  assert.throws(() => verify(model(), NOW + 120_001), /catalog_stale/);
  assert.throws(() => verify(model(), Number.NaN), /catalog_stale/);
});

test("价格检查读取的是注入的本地缓存，不访问网络", async () => {
  const result = await requireCurrentFreeModel(DEFAULT_MODEL, {
    now: () => NOW,
    fileReader: async () => JSON.stringify(catalog()),
    statReader: async () => ({ isFile: () => true, mtimeMs: NOW - 1000 }),
  });
  assert.equal(result.priceVerified, true);
  await assert.rejects(requireCurrentFreeModel(DEFAULT_MODEL, {
    fileReader: async () => { throw Error("missing-cache"); },
    statReader: async () => ({ isFile: () => true, mtimeMs: NOW }),
  }), /free_catalog_unavailable/);
  await assert.rejects(requireCurrentFreeModel(DEFAULT_MODEL, {
    now: () => NOW,
    fileReader: async () => JSON.stringify(catalog()),
    statReader: async () => ({ isFile: () => false, mtimeMs: NOW }),
  }), /catalog_invalid_file/);
});

test("未获授权时 runOpenCode 不启动子进程、不查价、不调用模型", async () => {
  let gateCalled = false;
  await assert.rejects(runOpenCode("hello", {
    liveApproved: false,
    priceGate: async () => { gateCalled = true; },
    bin: "/bin/false",
  }), /live_model_not_authorized/);
  assert.equal(gateCalled, false);
  await assert.rejects(runOpenCode("hello", {
    liveApproved: true,
    priceGate: async () => { throw Error("opencode_model_not_verified_free"); },
    bin: "/bin/false",
  }), /not_verified_free/);
});

test("任务模型通过严格白名单传到运行器，并生成真实文本产物", async () => {
  const dir = await mkdtemp(join(tmpdir(), "opencode-free-pool-"));
  try {
    let chosen;
    await handleTask({
      task: { task_id: "synthetic", title: "Review", prompt: "Offline analysis",
              workspace: dir, metadata: { team_phase: "review",
                  opencode_model: "opencode/nemotron-3.5-lightning-free",
                  expected_artifacts: ["review.md"] } },
      getMessages: async () => [],
      reportProgress: async () => {},
    }, { run: async (_, options) => {
      chosen = options.model;
      return { text: "## Accepted\nOffline mock verdict" };
    } });
    assert.equal(chosen, "opencode/nemotron-3.5-lightning-free");
    assert.match(await readFile(join(dir, "review.md"), "utf8"), /Offline mock/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
