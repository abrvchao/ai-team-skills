import test from "node:test";
import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  collectArtifacts,
  createTaskHandler,
  finalizePromptFor,
  initializeRequiredArtifacts,
  isFinalizeMessage,
  preflightModel,
  promptArtifacts,
  requiredArtifacts,
  runGeminiPrompt,
} from "../src/index.js";
import {
  BOOTSTRAP_TOKEN_REQUIRED,
  READINESS_TOKEN,
  requireBootstrapToken,
  startGeminiWorker,
} from "../src/readiness.js";

async function tempWorkspace() {
  return mkdtemp(join(tmpdir(), "gemini-worker-"));
}

test("prompt artifact discovery and CN-GEMINI-001 required output", () => {
  assert.deepEqual(
    promptArtifacts("write docs/a.md and output/report.json"),
    ["docs/a.md", "output/report.json"],
  );
  assert.deepEqual(
    requiredArtifacts({
      task_id: "x",
      metadata: { logical_task_id: "CN-GEMINI-001" },
    }),
    ["docs/research/china-social/xiaohongshu-data-options.md"],
  );
});

test("fake Gemini executable runs headlessly and emits stream-json events", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-gemini");
  try {
    await writeFile(
      fake,
      `#!/usr/bin/env node
const args = process.argv.slice(2);
console.log(JSON.stringify({type:"init", model:"fake-gemini", args}));
console.log(JSON.stringify({type:"tool_use", tool_name:"write_file"}));
console.log(JSON.stringify({type:"result", response:"done"}));
`,
      "utf8",
    );
    await chmod(fake, 0o755);

    const seen = [];
    const result = await runGeminiPrompt({
      task: {
        task_id: "task_fake",
        title: "Fake",
        workspace: root,
        prompt: "do it",
      },
      prompt: "hello",
      geminiBin: fake,
      authGate: async () => ({ auth: "synthetic_test" }),
      onEvent: async (event) => seen.push(event),
      timeoutMs: 5_000,
    });

    assert.deepEqual(seen.map((event) => event.type), [
      "init",
      "tool_use",
      "result",
    ]);
    assert.match(result.stdout, /fake-gemini/);
    assert.ok(seen[0].args.includes("--prompt"));
    assert.ok(seen[0].args.includes("--output-format"));
    assert.ok(seen[0].args.includes("stream-json"));
    assert.ok(seen[0].args.includes("--approval-mode"));
    assert.ok(seen[0].args.includes("plan"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("preflight accepts only exact Gemini readiness response", async () => {
  const result = await preflightModel({
    runPrompt: async () => ({
      stdout: JSON.stringify({ response: READINESS_TOKEN }) + "\n",
      events: [{ response: READINESS_TOKEN }],
    }),
  });
  assert.equal(result.token, READINESS_TOKEN);

  await assert.rejects(
    () =>
      preflightModel({
        runPrompt: async () => ({
          stdout: JSON.stringify({ response: "almost " + READINESS_TOKEN }) + "\n",
          events: [{ response: "almost " + READINESS_TOKEN }],
        }),
      }),
    (error) => error.code === "readiness_token_mismatch",
  );
});

test("missing bootstrap token fails before registration", async () => {
  assert.throws(
    () => requireBootstrapToken(""),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  let factoryCalls = 0;
  await assert.rejects(
    () =>
      startGeminiWorker({
        client: { reportReadiness: async () => {} },
        bootstrapToken: "",
        preflight: async () => {},
        workerFactory: () => {
          factoryCalls += 1;
          return { start: async () => {} };
        },
      }),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  assert.equal(factoryCalls, 0);
});

test("task handler maps progress and validates required artifact", async () => {
  const root = await tempWorkspace();
  try {
    const required = join(
      root,
      "docs/research/china-social/xiaohongshu-data-options.md",
    );
    const progress = [];
    const handler = createTaskHandler({
      runPrompt: async ({ onEvent }) => {
        await mkdir(join(root, "docs/research/china-social"), { recursive: true });
        await writeFile(required, "# Xiaohongshu data options\n", "utf8");
        await onEvent({ type: "init", model: "fake" });
        await onEvent({ type: "tool_use", tool_name: "write_file" });
        await onEvent({ type: "result", response: "done" });
        return { stdout: "", stderr: "", events: [] };
      },
    });

    const ctx = {
      task: {
        task_id: "task_gemini_1",
        title: "CN-GEMINI-001",
        workspace: root,
        prompt: "Research Xiaohongshu and write docs/research/china-social/xiaohongshu-data-options.md",
        metadata: { logical_task_id: "CN-GEMINI-001" },
      },
      signal: new AbortController().signal,
      getMessages: () => [],
      refreshMessages: async () => [],
      onMessage: () => () => {},
      reportProgress: async (event) => progress.push(event),
    };

    const result = await handler(ctx);
    assert.equal(result.artifacts.length, 1);
    assert.equal(
      result.artifacts[0].path,
      "docs/research/china-social/xiaohongshu-data-options.md",
    );
    assert.deepEqual(
      progress.filter((event) => event.current).map((event) => event.current),
      [1, 2, 2, 4, 5],
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("Gemini finalization message aborts exploration and runs bounded artifact finalizer", async () => {
  const root = await tempWorkspace();
  let messageHandler;
  const calls = [];
  const progress = [];
  try {
    const handler = createTaskHandler({
      runPrompt: async ({ prompt, signal, timeoutMs, onEvent }) => {
        calls.push({ prompt, timeoutMs });
        if (calls.length === 1) {
          await new Promise((resolve, reject) => {
            if (signal.aborted) {
              const error = new Error("aborted"); error.name = "AbortError"; reject(error); return;
            }
            signal.addEventListener("abort", () => {
              const error = new Error("aborted"); error.name = "AbortError"; reject(error);
            }, { once: true });
          });
          return { stdout: "", stderr: "", events: [] };
        }
        assert.match(prompt, /FINALIZATION MODE/);
        assert.match(prompt, /Do NOT invoke sub-agents/);
        assert.equal(timeoutMs, 45_000);
        await mkdir(join(root, "validation"), { recursive: true });
        await writeFile(
          join(root, "validation/validation_report.md"),
          "## ACCEPTED_CHECKS\n- observed\n## FAILED_CHECKS\n- none\n## DIFF_SCOPE\n- scoped\n## TEST_EVIDENCE\n- existing evidence\n## REQUIRED_REPAIR\n- none\nVERDICT: accept\n",
          "utf8",
        );
        await onEvent({ type: "result", response: "finalized" });
        return { stdout: "", stderr: "", events: [] };
      },
    });
    const ctx = {
      task: {
        task_id: "gemini-finalize", title: "Validate", workspace: root, prompt: "RUN_MORE_TOOLS",
        metadata: {
          team_phase: "validation",
          expected_artifacts: ["validation/validation_report.md"],
          context_excerpt: "RADAR_CONTEXT_C001 source-run-36f",
          execution_budget: {
            exploration: { timeout_seconds: 180, max_tool_calls: 32 },
            finalization: { timeout_seconds: 45, max_tool_calls: 16, allow_new_exploration: false },
          },
        },
      },
      signal: new AbortController().signal,
      getMessages: () => [],
      refreshMessages: async () => [],
      onMessage: (handlerFn) => { messageHandler = handlerFn; return () => {}; },
      reportProgress: async (event) => progress.push(event),
    };
    const running = handler(ctx);
    await new Promise((resolve) => setTimeout(resolve, 5));
    await messageHandler({ content: "Supervisor soft deadline reached. Converge now and emit the mandatory artifact." });
    const result = await running;
    assert.equal(calls.length, 2);
    assert.equal(calls[0].timeoutMs, 180_000);
    assert.equal(calls[1].timeoutMs, 45_000);
    assert.match(calls[1].prompt, /RADAR_CONTEXT_C001 source-run-36f/);
    assert.ok(progress.some((event) => event.stage === "forced_finalization"));
    assert.equal(result.artifacts[0].path, "validation/validation_report.md");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Gemini phase-aware finalization prompt forbids new validation exploration", () => {
  const task = {
    task_id: "gemini-validation-finalize", title: "Validate", prompt: "RUN_ALL_TESTS",
    metadata: { team_phase: "validation", expected_artifacts: ["validation_report.md"] },
  };
  const prompt = finalizePromptFor(task, [{ content: "finalize now" }]);
  assert.match(prompt, /FINALIZATION MODE/);
  assert.match(prompt, /Stop all validation commands/);
  assert.match(prompt, /VERDICT: accept \| repair \| blocked/);
  assert.doesNotMatch(prompt, /RUN_ALL_TESTS/);
  assert.equal(isFinalizeMessage("FINALIZATION HARD BOUNDARY"), true);
});

test("Gemini initializes mandatory text artifacts as in-progress checkpoints", async () => {
  const root = await tempWorkspace();
  try {
    const task = {
      task_id: "gemini-checkpoint", title: "Checkpoint", workspace: root,
      metadata: { expected_artifacts: ["nested/report.md"] },
    };
    const initialized = await initializeRequiredArtifacts(task);
    assert.deepEqual(initialized, ["nested/report.md"]);
    const fs = await import("node:fs/promises");
    assert.match(await fs.readFile(join(root, "nested/report.md"), "utf8"), /AI_TEAM_ARTIFACT_STATUS: in_progress/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("missing required Gemini artifact fails instead of pretending completion", async () => {
  const root = await tempWorkspace();
  try {
    await assert.rejects(
      () =>
        collectArtifacts({
          task_id: "task_gemini_2",
          workspace: root,
          prompt: "",
          metadata: { logical_task_id: "CN-GEMINI-001" },
        }),
      /Expected artifact was not created/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Gemini refuses an in-progress mandatory artifact as completed output", async () => {
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
