import test from "node:test";
import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  buildTraeArgs,
  collectArtifacts,
  createTaskHandler,
  preflightModel,
  runDoubaoTask,
} from "../src/index.js";
import {
  BOOTSTRAP_TOKEN_REQUIRED,
  CREDENTIAL_UNAVAILABLE,
  QUOTA_EXCEEDED,
  READINESS_TOKEN,
  requireBootstrapToken,
  requireProviderCredential,
  startDoubaoWorker,
  waitForModel,
} from "../src/readiness.js";

async function tempWorkspace() {
  return mkdtemp(join(tmpdir(), "doubao-worker-"));
}

test("Trae args use config/provider/model/working-dir without API key", () => {
  const args = buildTraeArgs({ promptFile: "/tmp/prompt.txt", workspace: "/tmp/work" });
  assert.ok(args.includes("--provider"));
  assert.ok(args.includes("doubao"));
  assert.ok(args.includes("--model"));
  assert.ok(args.includes("--working-dir"));
  assert.ok(args.includes("/tmp/work"));
  assert.ok(args.includes("--config-file"));
  assert.equal(args.includes("--api-key"), false);
});

test("Doubao API preflight requires exact token and keeps key in header", async () => {
  const previous = process.env.DOUBAO_API_KEY;
  process.env.DOUBAO_API_KEY = "unit-secret";
  try {
    const result = await preflightModel({
      fetchImpl: async (url, options) => {
        assert.match(String(url), /chat\/completions$/);
        assert.equal(options.headers.authorization, "Bearer unit-secret");
        const body = JSON.parse(options.body);
        assert.equal(body.messages[0].content, "Return exactly: DOUBAO_MODEL_OK");
        return {
          ok: true,
          status: 200,
          async json() { return { choices: [{ message: { content: READINESS_TOKEN } }] }; },
        };
      },
    });
    assert.equal(result.token, READINESS_TOKEN);
    await assert.rejects(
      () => preflightModel({
        fetchImpl: async () => ({
          ok: true,
          status: 200,
          async json() { return { choices: [{ message: { content: "wrong" } }] }; },
        }),
      }),
      (error) => error.code === "readiness_token_mismatch",
    );
  } finally {
    if (previous === undefined) delete process.env.DOUBAO_API_KEY;
    else process.env.DOUBAO_API_KEY = previous;
  }
});

test("fake Trae executable receives Doubao key in env and exposes tool progress", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-trae");
  const previous = process.env.DOUBAO_API_KEY;
  process.env.DOUBAO_API_KEY = "unit-secret";
  try {
    await writeFile(fake, `#!/usr/bin/env node
if (process.env.DOUBAO_API_KEY !== "unit-secret") process.exit(41);
if (process.argv.includes("--api-key")) process.exit(42);
console.log("Tools  bash");
console.log("Final Result done");
`, "utf8");
    await chmod(fake, 0o755);
    const tools = [];
    const result = await runDoubaoTask({
      task: { task_id: "fake", title: "fake", workspace: root, prompt: "x" },
      prompt: "hello",
      traeBin: fake,
      onTool: async (name) => tools.push(name),
      timeoutMs: 5_000,
    });
    assert.deepEqual(tools, ["bash"]);
    assert.match(result.stdout, /Final Result/);
  } finally {
    if (previous === undefined) delete process.env.DOUBAO_API_KEY;
    else process.env.DOUBAO_API_KEY = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("missing provider credential is explicit", () => {
  assert.throws(() => requireProviderCredential(""), (error) => error.code === CREDENTIAL_UNAVAILABLE);
});

test("Volcano Ark SetLimitExceeded maps to quota_exceeded without leaking provider response", async () => {
  const before = process.env.DOUBAO_API_KEY;
  process.env.DOUBAO_API_KEY = "test-only-secret";
  try {
    await assert.rejects(
      () => preflightModel({
        fetchImpl: async () => ({
          ok: false,
          status: 429,
          async text() {
            return JSON.stringify({
              error: { code: "SetLimitExceeded", message: "secret_account_identifier" },
            });
          },
        }),
      }),
      (error) => {
        assert.equal(error.code, QUOTA_EXCEEDED);
        assert.equal(error.httpStatus, 429);
        assert.doesNotMatch(error.message, /secret_account_identifier|test-only-secret/);
        return true;
      },
    );
  } finally {
    if (before === undefined) delete process.env.DOUBAO_API_KEY;
    else process.env.DOUBAO_API_KEY = before;
  }
});

test("Quota exceeded reports not-ready once, never retries or registers worker", async () => {
  const reports = [];
  let attempts = 0;
  let workerStarts = 0;
  const failure = new Error("quota reached");
  failure.code = QUOTA_EXCEEDED;
  const result = await startDoubaoWorker({
    client: { reportReadiness: async (data) => reports.push(data) },
    bootstrapToken: "test-only-bootstrap",
    preflight: async () => { attempts += 1; throw failure; },
    workerFactory: () => ({ start: async () => { workerStarts += 1; } }),
    sleepImpl: async () => { throw new Error("must not retry quota error"); },
  });
  assert.equal(result.started, false);
  assert.equal(result.readiness.reason, QUOTA_EXCEEDED);
  assert.equal(attempts, 1);
  assert.equal(workerStarts, 0);
  assert.equal(reports.length, 1);
  assert.equal(reports[0].modelAvailable, false);
  assert.equal(reports[0].reason, QUOTA_EXCEEDED);
});

test("missing bootstrap token fails before registration", async () => {
  assert.throws(() => requireBootstrapToken(""), (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED);
  let calls = 0;
  await assert.rejects(
    () => startDoubaoWorker({
      client: { reportReadiness: async () => {} },
      bootstrapToken: "",
      preflight: async () => {},
      workerFactory: () => { calls += 1; return { start: async () => {} }; },
    }),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  assert.equal(calls, 0);
});

test("task handler reports progress, tool use, and validates artifact", async () => {
  const root = await tempWorkspace();
  try {
    const progress = [];
    const handler = createTaskHandler({
      runTask: async ({ onTool }) => {
        await writeFile(join(root, "doubao-proof.txt"), "DOUBAO_WORKER_ONLINE\n", "utf8");
        await onTool("str_replace_based_edit_tool");
        return { stdout: "done", stderr: "" };
      },
    });
    const result = await handler({
      task: { task_id: "d", title: "d", workspace: root, prompt: "Create doubao-proof.txt", metadata: { expected_artifacts: ["doubao-proof.txt"] } },
      signal: new AbortController().signal,
      getMessages: () => [],
      refreshMessages: async () => [],
      onMessage: () => () => {},
      reportProgress: async (event) => progress.push(event),
    });
    assert.equal(result.artifacts[0].path, "doubao-proof.txt");
    assert.deepEqual(progress.filter((e) => e.current).map((e) => e.current), [1, 2, 4, 5]);
    assert.ok(progress.some((e) => e.stage === "tool_use"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("Supervisor finalize interrupts Doubao exploration and uses protected finalization budget", async () => {
  const root = await tempWorkspace();
  try {
    let messageHandler;
    const calls = [];
    const progress = [];
    const handler = createTaskHandler({
      runTask: async ({ signal, prompt, timeoutMs, maxSteps }) => {
        calls.push({ prompt, timeoutMs, maxSteps });
        if (calls.length === 1) {
          await new Promise((resolve, reject) => {
            signal.addEventListener("abort", () => { const error = new Error("aborted"); error.name = "AbortError"; reject(error); }, { once: true });
          });
        } else {
          await writeFile(join(root, "primary.md"), "# finalized\n", "utf8");
        }
        return { stdout: "", stderr: "" };
      },
    });
    const ctx = {
      task: {
        task_id: "doubao-finalize", title: "Finalize", workspace: root, prompt: "RUN_MORE_RESEARCH then create primary.md",
        metadata: {
          expected_artifacts: ["primary.md"],
          context_excerpt: "RADAR_CONTEXT_C001 source-run-36f",
          execution_budget: {
            exploration: { timeout_seconds: 180, max_tool_calls: 24 },
            finalization: { timeout_seconds: 45, max_tool_calls: 16, allow_new_exploration: false },
          },
        },
      },
      signal: new AbortController().signal,
      getMessages: () => [],
      refreshMessages: async () => [],
      onMessage: (fn) => { messageHandler = fn; return () => {}; },
      reportProgress: async (event) => progress.push(event),
    };
    const running = handler(ctx);
    await new Promise((resolve) => setTimeout(resolve, 5));
    await messageHandler({ content: "Supervisor soft deadline reached. Converge now and finalize." });
    const result = await running;
    assert.equal(calls.length, 2);
    assert.deepEqual({ timeoutMs: calls[0].timeoutMs, maxSteps: calls[0].maxSteps }, { timeoutMs: 180_000, maxSteps: 24 });
    assert.deepEqual({ timeoutMs: calls[1].timeoutMs, maxSteps: calls[1].maxSteps }, { timeoutMs: 45_000, maxSteps: 16 });
    assert.match(calls[1].prompt, /FINALIZATION MODE/);
    assert.match(calls[1].prompt, /RADAR_CONTEXT_C001 source-run-36f/);
    assert.doesNotMatch(calls[1].prompt, /RUN_MORE_RESEARCH/);
    assert.ok(progress.some((event) => event.stage === "forced_finalization"));
    assert.equal(result.artifacts[0].path, "primary.md");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("missing required Doubao artifact fails", async () => {
  const root = await tempWorkspace();
  try {
    await assert.rejects(
      () => collectArtifacts({ workspace: root, prompt: "", metadata: { expected_artifacts: ["missing.txt"] } }),
      /Expected artifact was not created/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Doubao refuses an in-progress mandatory artifact as completed output", async () => {
  const root = await tempWorkspace();
  try {
    await writeFile(join(root, "primary.md"), "AI_TEAM_ARTIFACT_STATUS: in_progress\n# checkpoint\n", "utf8");
    await assert.rejects(
      () => collectArtifacts({ workspace: root, prompt: "", metadata: { expected_artifacts: ["primary.md"] } }),
      /still an in-progress checkpoint/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
