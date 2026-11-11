import test from "node:test";
import assert from "node:assert/strict";
import { startGrokFromEnvironment } from "../src/index.js";
import { startGrokWorker } from "../src/readiness.js";

test("付费 Grok 未显式授权时不得运行任何预检或 Bridge 调用", async () => {
  const previous = process.env.AI_TEAM_GROK_PAID_CALLS_OPT_IN;
  delete process.env.AI_TEAM_GROK_PAID_CALLS_OPT_IN;
  let preflightCalls = 0;
  let bridgeCalls = 0;
  try {
    await assert.rejects(() => startGrokFromEnvironment({
      bootstrapToken: "fake-test-token",
      client: { reportReadiness: async () => { bridgeCalls += 1; } },
      preflight: async () => { preflightCalls += 1; },
    }), (error) => error.code === "grok_paid_calls_disabled");
    assert.equal(preflightCalls, 0);
    assert.equal(bridgeCalls, 0);
  } finally {
    if (previous === undefined) delete process.env.AI_TEAM_GROK_PAID_CALLS_OPT_IN;
    else process.env.AI_TEAM_GROK_PAID_CALLS_OPT_IN = previous;
  }
});

test("Bridge 拒绝报告就绪后最多调用一次模型，不进入无限循环", async () => {
  let calls = 0;
  const result = await startGrokWorker({
    bootstrapToken: "fake-test-token",
    client: { reportReadiness: async () => { throw new Error("invalid bootstrap token"); } },
    preflight: async () => { calls += 1; },
    logger: { error() {} },
    workerFactory: () => { throw new Error("Worker cannot start when readiness failed"); },
  });
  assert.equal(result.started, false);
  assert.equal(result.readiness.attempts, 1);
  assert.equal(calls, 1);
});
