import test from "node:test";
import assert from "node:assert/strict";
import { startQwenWorker } from "../../qwen-node-worker/src/readiness.js";
import { startWorkBuddyWorker } from "../../workbuddy-node-worker/src/readiness.js";
import { startDshWorker } from "../../dsh-node-worker/src/readiness.js";

// 此回归只用虚构令牌和模拟 Bridge，不调用任何真实模型 API。
for (const [name, start] of [
  ["Qwen", startQwenWorker],
  ["WorkBuddy", startWorkBuddyWorker],
  ["DSH", startDshWorker],
]) {
  test(name + " Bridge认证失败时默认最多进行一次模型预检", async () => {
    let modelCalls = 0;
    let workerStarted = false;
    const result = await start({
      bootstrapToken: "offline-test-fake-token",
      client: {
        reportReadiness: async () => {
          throw Object.assign(new Error("Bridge authentication failed"), { code: "invalid_bootstrap_token" });
        },
      },
      preflight: async () => { modelCalls += 1; },
      workerFactory: () => {
        workerStarted = true;
        throw new Error("Worker must not start after bad Bridge authentication");
      },
    });
    assert.equal(modelCalls, 1);
    assert.equal(workerStarted, false);
    assert.equal(result.started, false);
    assert.equal(result.readiness.attempts, 1);
  });
}
