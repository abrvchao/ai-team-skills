import test from "node:test";
import assert from "node:assert/strict";

import {
  MODEL_UNAVAILABLE,
  READINESS_PROMPT,
  READINESS_TOKEN,
  isReadinessResponse,
  runModelPreflight,
  startDshWorker,
} from "../src/readiness.js";

function fakeClient() {
  return {
    readiness: [],
    registerCalls: 0,
    async reportReadiness(payload) {
      this.readiness.push(payload);
      return payload;
    },
    async register() {
      this.registerCalls += 1;
      return { worker_id: "worker_test" };
    },
  };
}

test("exact readiness token passes", async () => {
  const result = await runModelPreflight({
    runPrompt: async ({ prompt }) => {
      assert.equal(prompt, READINESS_PROMPT);
      return { stdout: `${READINESS_TOKEN}\n` };
    },
  });
  assert.equal(result.token, READINESS_TOKEN);
});

test("exit=0 but wrong response content fails readiness", async () => {
  await assert.rejects(
    () =>
      runModelPreflight({
        runPrompt: async () => ({ stdout: "PI_AI_ERROR: servers overloaded\n" }),
      }),
    (error) => error.code === "readiness_token_mismatch",
  );
});

test("exit=0 with the token embedded in other text fails readiness", async () => {
  await assert.rejects(
    () =>
      runModelPreflight({
        runPrompt: async () => ({ stdout: `here you go: ${READINESS_TOKEN} — done` }),
      }),
    (error) => error.code === "readiness_token_mismatch",
  );
});

test("empty output fails readiness", async () => {
  await assert.rejects(
    () => runModelPreflight({ runPrompt: async () => ({ stdout: "   \n" }) }),
    (error) => error.code === "readiness_token_mismatch",
  );
});

test("readiness timeout fails and aborts the prompt signal", async () => {
  let observedSignal;
  const started = Date.now();
  await assert.rejects(
    () =>
      runModelPreflight({
        timeoutMs: 50,
        runPrompt: ({ signal }) => {
          observedSignal = signal;
          return new Promise(() => {});
        },
      }),
    (error) => error.code === "readiness_timeout",
  );
  assert.equal(observedSignal.aborted, true);
  assert.ok(Date.now() - started < 5_000);
});

test("unavailable model => no registration, available=false, reason=model_unavailable", async () => {
  const client = fakeClient();
  const result = await startDshWorker({
    client,
    preflight: async () => {
      const error = new Error("provider unavailable");
      error.code = "readiness_timeout";
      throw error;
    },
    workerFactory: () => {
      throw new Error("worker factory must not run for an unhealthy model");
    },
    maxAttempts: 1,
    sleepImpl: async () => {},
  });
  assert.equal(result.started, false);
  assert.equal(client.registerCalls, 0);
  assert.deepEqual(client.readiness.at(-1), {
    agentId: "dsh",
    modelAvailable: false,
    reason: MODEL_UNAVAILABLE,
  });
});

test("registration happens only after a successful preflight", async () => {
  const order = [];
  const client = {
    async reportReadiness(payload) {
      order.push(payload.modelAvailable ? "readiness:available" : "readiness:unavailable");
    },
    async register() {
      order.push("register");
      return { worker_id: "worker_test" };
    },
  };
  const result = await startDshWorker({
    client,
    preflight: async () => {
      order.push("preflight");
    },
    workerFactory: () => ({
      async start() {
        order.push("worker.start");
        await client.register();
      },
    }),
    sleepImpl: async () => {},
  });
  assert.equal(result.started, true);
  assert.deepEqual(order, ["preflight", "readiness:available", "worker.start", "register"]);
});

test("an unhealthy model never reaches the worker factory", async () => {
  let factoryCalls = 0;
  const client = fakeClient();
  await startDshWorker({
    client,
    preflight: async () => {
      throw new Error("boom");
    },
    workerFactory: () => {
      factoryCalls += 1;
      return { async start() {} };
    },
    maxAttempts: 2,
    sleepImpl: async () => {},
  });
  assert.equal(factoryCalls, 0);
  assert.equal(client.readiness.length, 2);
  assert.ok(client.readiness.every((entry) => entry.modelAvailable === false));
});

test("token matching normalizes insignificant formatting only", () => {
  assert.equal(isReadinessResponse("  DSH_MODEL_OK  "), true);
  assert.equal(isReadinessResponse("`DSH_MODEL_OK`"), true);
  assert.equal(isReadinessResponse("DSH_MODEL_OKAY"), false);
  assert.equal(isReadinessResponse("not DSH_MODEL_OK"), false);
  assert.equal(isReadinessResponse(""), false);
});
