import test from "node:test";
import assert from "node:assert/strict";

import {
  BOOTSTRAP_TOKEN_REQUIRED,
  MODEL_UNAVAILABLE,
  READINESS_PROMPT,
  READINESS_TOKEN,
  isReadinessResponse,
  requireBootstrapToken,
  runModelPreflight,
  waitForModel,
  startDshWorker,
} from "../src/readiness.js";

const TEST_TOKEN = "test-bootstrap-token";

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
    bootstrapToken: TEST_TOKEN,
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
    bootstrapToken: TEST_TOKEN,
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
    bootstrapToken: TEST_TOKEN,
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
    bootstrapToken: TEST_TOKEN,
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

test("missing bootstrap token fails fast instead of looping", async () => {
  let preflightCalls = 0;
  let factoryCalls = 0;
  let sleeps = 0;
  const client = fakeClient();

  await assert.rejects(
    () =>
      startDshWorker({
        client,
        bootstrapToken: "",
        preflight: async () => {
          preflightCalls += 1;
        },
        workerFactory: () => {
          factoryCalls += 1;
          return { async start() {} };
        },
        maxAttempts: Infinity,
        sleepImpl: async () => {
          sleeps += 1;
        },
      }),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );

  assert.equal(preflightCalls, 0, "must not run a preflight without a token");
  assert.equal(factoryCalls, 0, "must not create a worker without a token");
  assert.equal(sleeps, 0, "must not enter the retry loop");
  assert.deepEqual(client.readiness, [], "must not attempt an unauthenticated report");
});

test("missing bootstrap token is rejected before the readiness loop", async () => {
  const client = fakeClient();
  await assert.rejects(
    () => waitForModel({ client, bootstrapToken: "   ", preflight: async () => {} }),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  assert.deepEqual(client.readiness, []);
});

test("requireBootstrapToken returns a trimmed token and rejects blanks", () => {
  assert.equal(requireBootstrapToken("  abc  "), "abc");
  for (const value of ["", "   ", undefined, null]) {
    assert.throws(
      () => requireBootstrapToken(value),
      (error) =>
        error.code === BOOTSTRAP_TOKEN_REQUIRED && /AI_TEAM_BOOTSTRAP_TOKEN/.test(error.message),
    );
  }
});

test("environment startup path fails before preflight when no token is configured", async () => {
  const { startDshFromEnvironment } = await import("../src/index.js");
  let preflightCalls = 0;
  let factoryCalls = 0;
  const client = fakeClient();

  const previousToken = process.env.AI_TEAM_BOOTSTRAP_TOKEN;
  const previousFile = process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE;
  delete process.env.AI_TEAM_BOOTSTRAP_TOKEN;
  delete process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE;
  try {
    await assert.rejects(
      () =>
        startDshFromEnvironment({
          client,
          preflight: async () => {
            preflightCalls += 1;
          },
          workerFactory: () => {
            factoryCalls += 1;
            return { async start() {} };
          },
        }),
      (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
    );
  } finally {
    if (previousToken === undefined) delete process.env.AI_TEAM_BOOTSTRAP_TOKEN;
    else process.env.AI_TEAM_BOOTSTRAP_TOKEN = previousToken;
    if (previousFile === undefined) delete process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE;
    else process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE = previousFile;
  }

  assert.equal(preflightCalls, 0);
  assert.equal(factoryCalls, 0);
  assert.deepEqual(client.readiness, []);
});

test("environment startup path proceeds once a token is configured", async () => {
  const { startDshFromEnvironment } = await import("../src/index.js");
  const order = [];
  const client = {
    async reportReadiness() {
      order.push("readiness");
    },
    async register() {
      order.push("register");
    },
  };
  const result = await startDshFromEnvironment({
    client,
    bootstrapToken: TEST_TOKEN,
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
  assert.equal(client.bootstrapToken, TEST_TOKEN);
  assert.deepEqual(order, ["preflight", "readiness", "worker.start", "register"]);
});
