import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

// 所有 fetch 均替换为本地合成响应；本测试绝不触达模型服务商。
process.env.MODEL_WORKER_ID = "mimo";
const { callModel, paidPreflightEnabled } = await import("../src/index.js");
const root = resolve(import.meta.dirname, "../..");
const cliPython = process.env.AI_TEAM_COST_GATE_PYTHON || "/opt/homebrew/bin/python3";

const billingVars = [
  "AI_TEAM_MODEL_CALLS_OPT_IN", "AI_TEAM_MODEL_PREFLIGHT_OPT_IN",
  "AI_TEAM_COST_LEDGER_DB", "AI_TEAM_MAX_CALL_COST_MICROS",
  "AI_TEAM_COST_CURRENCY", "MODEL_API_KEY_FILE",
];
function withEnvironment(values, fn) {
  const before = Object.fromEntries(billingVars.map((name) => [name, process.env[name]]));
  const fetchBefore = globalThis.fetch;
  Object.assign(process.env, values);
  return Promise.resolve().then(fn).finally(() => {
    for (const key of billingVars) {
      if (before[key] === undefined) delete process.env[key];
      else process.env[key] = before[key];
    }
    globalThis.fetch = fetchBefore;
  });
}

function installSyntheticPolicy(db) {
  // 人工审批仅是合成样例，不向用户账本或真实服务商写入额度。
  const program = [
    "from cost_ledger import BudgetLedger",
    "import sys",
    "BudgetLedger(sys.argv[1]).install_policy(",
    " currency='USD', total_cap_micros=10,",
    " provider_caps={'mimo': 10},",
    " approval_ref='offline-synthetic-test', approved_by_user=True)",
  ].join("\n");
  execFileSync(cliPython, ["-c", program, db], { cwd: root, timeout: 8000 });
}

test("未明确授权时，真实请求与启动预检都不执行", async () => {
  let calls = 0;
  await withEnvironment({ AI_TEAM_MODEL_CALLS_OPT_IN: "0", AI_TEAM_MODEL_PREFLIGHT_OPT_IN: "0" }, async () => {
    globalThis.fetch = async () => { calls++; throw Error("unexpected_network_call"); };
    assert.equal(paidPreflightEnabled(), false);
    await assert.rejects(
      callModel("synthetic", { requestId: "trial1", taskId: "trial1" }),
      /model_paid_calls_disabled/,
    );
  });
  assert.equal(calls, 0);
});

test("即使模型调用显式开关开启，未配置共享费用账本也拒绝", async () => {
  let calls = 0;
  await withEnvironment({
    AI_TEAM_MODEL_CALLS_OPT_IN: "1",
    AI_TEAM_COST_LEDGER_DB: "",
    AI_TEAM_MAX_CALL_COST_MICROS: "10",
    AI_TEAM_COST_CURRENCY: "USD",
  }, async () => {
    globalThis.fetch = async () => { calls++; throw Error("unexpected_network_call"); };
    await assert.rejects(callModel("synthetic", { requestId: "trial2", taskId: "trial2" }),
      /model_budget_not_configured/);
  });
  assert.equal(calls, 0);
});

test("首次授权可使用模拟 fetch；重放和超预算均无法再次请求模型", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "ai-team-compatible-gate-"));
  const db = join(tmp, "budget.sqlite");
  const keyfile = join(tmp, "synthetic.key");
  writeFileSync(keyfile, "synthetic-fixture-key");
  try {
    installSyntheticPolicy(db);
    let calls = 0;
    await withEnvironment({
      AI_TEAM_MODEL_CALLS_OPT_IN: "1",
      AI_TEAM_COST_LEDGER_DB: db,
      AI_TEAM_MAX_CALL_COST_MICROS: "10",
      AI_TEAM_COST_CURRENCY: "USD",
      MODEL_API_KEY_FILE: keyfile,
    }, async () => {
      globalThis.fetch = async (_url, _request) => {
        calls++;
        return { ok: true, json: async () => ({
          choices: [{ message: { content: "synthetic success" } }], usage: { total_tokens: 2 },
        }) };
      };
      const first = await callModel("synthetic", { taskId: "task-01", requestId: "model-task-01" });
      assert.equal(first.text, "synthetic success");
      await assert.rejects(
        callModel("synthetic", { taskId: "task-01", requestId: "model-task-01" }),
        /model_budget_denied/,
      );
      await assert.rejects(
        callModel("synthetic", { taskId: "task-02", requestId: "model-task-02" }),
        /model_budget_denied/,
      );
    });
    assert.equal(calls, 1);
  } finally {
    rmSync(tmp, { force: true, recursive: true });
  }
});

test("启动入口默认拒绝远程预检，不读取密钥或请求外网", () => {
  const script = resolve(root, "compatible-node-worker/src/index.js");
  const child = spawnSync(process.execPath, [script], {
    env: {
      ...process.env, MODEL_WORKER_ID: "mimo",
      MODEL_API_KEY_FILE: "/not-existing/synthetic",
      AI_TEAM_BOOTSTRAP_TOKEN_FILE: "/not-existing/synthetic",
      AI_TEAM_MODEL_PREFLIGHT_OPT_IN: "0",
      AI_TEAM_MODEL_CALLS_OPT_IN: "0",
    },
    timeout: 8000,
    encoding: "utf8",
  });
  assert.notEqual(child.status, 0);
  assert.match(child.stderr, /paid_model_preflight_disabled/);
  assert.doesNotMatch(child.stderr, /http_|network/);
});
