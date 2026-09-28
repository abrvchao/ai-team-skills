import test from "node:test";
import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  collectArtifacts,
  createTaskHandler,
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
    assert.ok(seen[0].args.includes("auto_edit"));
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
      [1, 2, 3, 4],
    );
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
