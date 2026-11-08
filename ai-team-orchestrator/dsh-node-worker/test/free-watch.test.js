/** 免费资格检查必须覆盖 worker 成功运行的整个生命周期（离线测试）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { startWithFreeWatch } from "../src/index.js";

test("成功启动后保留免费资格监控，不再无条件 clear", async () => {
  let stopped = 0;
  const result = await startWithFreeWatch({
    start: async () => ({ started: true }),
    stopWatch: () => { stopped += 1; },
  });
  assert.equal(result.started, true);
  assert.equal(stopped, 0);
});

test("启动失败时清理监控并保持失败状态", async () => {
  let stopped = 0;
  const result = await startWithFreeWatch({
    start: async () => ({ started: false }),
    stopWatch: () => { stopped += 1; },
  });
  assert.equal(result.started, false);
  assert.equal(stopped, 1);
});

test("启动抛出异常时也要清理监控，不隐藏错误", async () => {
  let stopped = 0;
  await assert.rejects(
    startWithFreeWatch({
      start: async () => { throw new Error("synthetic_failure"); },
      stopWatch: () => { stopped += 1; },
    }),
    /synthetic_failure/,
  );
  assert.equal(stopped, 1);
});
