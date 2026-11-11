import test from "node:test";
import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  ARTIFACT_IN_PROGRESS_MARKER,
  buildQwenArgs,
  collectArtifacts,
  createTaskHandler,
  finalizePromptFor,
  persistFinalizationText,
  QWEN_FINALIZATION_EXCLUDED_TOOLS,
  initializeRequiredArtifacts,
  preflightModel,
  qwenProgressFromEvent,
  runQwenPrompt,
  runQwenTextFinalization,
} from "../src/index.js";
import {
  BOOTSTRAP_TOKEN_REQUIRED,
  CREDENTIAL_UNAVAILABLE,
  READINESS_TOKEN,
  requireBootstrapToken,
  requireProviderCredential,
  startQwenWorker,
} from "../src/readiness.js";

async function tempWorkspace() {
  return mkdtemp(join(tmpdir(), "qwen-worker-"));
}

test("Qwen args use bounded headless mode without embedding API key", () => {
  const args = buildQwenArgs({ prompt: "hello", outputFormat: "stream-json" });
  assert.ok(args.includes("--bare"));
  assert.ok(args.includes("--approval-mode"));
  assert.ok(args.includes("auto"));
  assert.ok(args.includes("--output-format"));
  assert.ok(args.includes("stream-json"));
  assert.ok(args.includes("--auth-type"));
  assert.ok(args.includes("openai"));
  assert.equal(args.some((v) => v.includes("sk-")), false);
  assert.ok(args.includes("--prompt"));
  const finalArgs = buildQwenArgs({ prompt: "finalize", outputFormat: "text", maxToolCalls: 0, excludeTools: QWEN_FINALIZATION_EXCLUDED_TOOLS });
  assert.ok(finalArgs.includes("--exclude-tools=read_file"));
  assert.ok(finalArgs.includes("--exclude-tools=run_shell_command"));
  assert.ok(finalArgs.includes("--exclude-tools=web_search"));
  assert.ok(finalArgs.includes("0"));
  assert.equal(finalArgs.at(-2), "--prompt");
  assert.equal(finalArgs.at(-1), "finalize");
});

test("fake Qwen executable emits stream-json and receives secret only in env", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-qwen");
  const previous = process.env.QWEN_API_KEY;
  process.env.QWEN_API_KEY = "unit-test-secret";
  try {
    await writeFile(fake, `#!/usr/bin/env node
const args=process.argv.slice(2);
if (process.env.OPENAI_API_KEY !== "unit-test-secret") process.exit(41);
console.log(JSON.stringify({type:"system",subtype:"session_start",model:"fake-qwen",args}));
console.log(JSON.stringify({type:"assistant",message:{content:[{type:"tool_use",name:"write_file"}]}}));
console.log(JSON.stringify({type:"result",subtype:"success",result:"done"}));
`, "utf8");
    await chmod(fake, 0o755);
    const seen = [];
    const result = await runQwenPrompt({
      task: { task_id: "fake", title: "fake", workspace: root, prompt: "do" },
      prompt: "hello",
      qwenBin: fake,
      onEvent: async (event) => seen.push(event),
      timeoutMs: 5_000,
    });
    assert.equal(seen.length, 3);
    assert.match(result.stdout, /session_start/);
    assert.equal(seen[0].args.includes("unit-test-secret"), false);
  } finally {
    if (previous === undefined) delete process.env.QWEN_API_KEY;
    else process.env.QWEN_API_KEY = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("Qwen stream events map to model and tool progress", () => {
  assert.deepEqual(
    qwenProgressFromEvent({ type: "system", subtype: "session_start", model: "qwen-test" }),
    { stage: "model_session", message: "Qwen session initialized (qwen-test)", current: 2, total: 4 },
  );
  assert.deepEqual(
    qwenProgressFromEvent({ type: "assistant", message: { content: [{ type: "tool_use", name: "write_file" }] } }),
    { stage: "tool_use", message: "Qwen is using write_file" },
  );
});

test("preflight requires exact Qwen token", async () => {
  const previous = process.env.QWEN_API_KEY;
  process.env.QWEN_API_KEY = "fake";
  try {
    const ok = await preflightModel({ runPrompt: async () => ({ stdout: READINESS_TOKEN + "\n" }) });
    assert.equal(ok.token, READINESS_TOKEN);
    await assert.rejects(
      () => preflightModel({ runPrompt: async () => ({ stdout: "almost " + READINESS_TOKEN }) }),
      (error) => error.code === "readiness_token_mismatch",
    );
  } finally {
    if (previous === undefined) delete process.env.QWEN_API_KEY;
    else process.env.QWEN_API_KEY = previous;
  }
});

test("missing provider credential is explicit", () => {
  assert.throws(() => requireProviderCredential(""), (error) => error.code === CREDENTIAL_UNAVAILABLE);
});

test("missing bootstrap token fails before registration", async () => {
  assert.throws(() => requireBootstrapToken(""), (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED);
  let calls = 0;
  await assert.rejects(
    () => startQwenWorker({
      client: { reportReadiness: async () => {} },
      bootstrapToken: "",
      preflight: async () => {},
      workerFactory: () => { calls += 1; return { start: async () => {} }; },
    }),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  assert.equal(calls, 0);
});


test("Qwen initializes mandatory text checkpoint without overwriting existing artifact", async () => {
  const root = await tempWorkspace();
  try {
    const task = { task_id: "checkpoint", title: "Checkpoint", workspace: root, prompt: "", metadata: { expected_artifacts: ["primary.md"] } };
    context_excerpt: "RADAR_CONTEXT_C001 source-run-36f",
    assert.deepEqual(await initializeRequiredArtifacts(task), ["primary.md"]);
    const fs = await import("node:fs/promises");
    assert.match(await fs.readFile(join(root, "primary.md"), "utf8"), new RegExp(ARTIFACT_IN_PROGRESS_MARKER));
    await fs.writeFile(join(root, "primary.md"), "# ready\n", "utf8");
    assert.deepEqual(await initializeRequiredArtifacts(task), []);
    assert.equal(await fs.readFile(join(root, "primary.md"), "utf8"), "# ready\n");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("task handler reports progress and validates a real artifact", async () => {
  const root = await tempWorkspace();
  try {
    const progress = [];
    const handler = createTaskHandler({
      runPrompt: async ({ onEvent }) => {
        await writeFile(join(root, "qwen-proof.txt"), "QWEN_WORKER_ONLINE\n", "utf8");
        await onEvent({ type: "system", subtype: "session_start", model: "fake" });
        await onEvent({ type: "assistant", message: { content: [{ type: "tool_use", name: "write_file" }] } });
        return { stdout: "", stderr: "", events: [] };
      },
    });
    const result = await handler({
      task: { task_id: "q", title: "q", workspace: root, prompt: "Create qwen-proof.txt", metadata: { expected_artifacts: ["qwen-proof.txt"] } },
      signal: new AbortController().signal,
      getMessages: () => [],
      refreshMessages: async () => [],
      onMessage: () => () => {},
      reportProgress: async (event) => progress.push(event),
    });
    assert.equal(result.artifacts[0].path, "qwen-proof.txt");
    assert.ok(progress.some((e) => e.stage === "artifact_initialized"));
    assert.ok(progress.some((e) => e.stage === "finalizing"));
    assert.ok(progress.some((e) => e.stage === "artifacts"));
    assert.ok(progress.some((e) => e.stage === "tool_use"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("Supervisor finalize interrupts active Qwen exploration and runs bounded finalization", async () => {
  const root = await tempWorkspace();
  try {
    let messageHandler;
    const progress = [];
    const calls = [];
    const handler = createTaskHandler({
      runPrompt: async ({ signal, prompt, maxToolCalls, timeoutMs, outputFormat }) => {
        calls.push({ kind: "exploration", prompt, maxToolCalls, timeoutMs, outputFormat });
        await new Promise((resolve, reject) => {
          if (signal.aborted) {
            const error = new Error("aborted"); error.name = "AbortError"; reject(error); return;
          }
          signal.addEventListener("abort", () => {
            const error = new Error("aborted"); error.name = "AbortError"; reject(error);
          }, { once: true });
        });
        return { stdout: "", stderr: "", events: [] };
      },
      runFinalization: async ({ prompt, timeoutMs }) => {
        calls.push({ kind: "finalization", prompt, timeoutMs });
        // 收尾测试使用独立纯文本通道，不向模型提供任何工具参数。
        return { stdout: "# decision ready\n\nEvidence gaps: none", stderr: "", events: [] };
      },
    });
    const ctx = {
      task: { task_id: "finalize-q", title: "Finalize Qwen", workspace: root, prompt: "MUST_RUN_TESTS_AND_SEARCH before Create primary.md", metadata: {
        expected_artifacts: ["primary.md"],
        context_excerpt: "RADAR_CONTEXT_C001 source-run-36f",
        task_contract: { objective: "Assess convergence", role_label: "Verifier" },
        execution_budget: {
          exploration: { timeout_seconds: 180, max_tool_calls: 28 },
          finalization: { timeout_seconds: 45, max_tool_calls: 16, allow_new_exploration: false },
        },
      } },
      signal: new AbortController().signal,
      getMessages: () => [],
      refreshMessages: async () => [],
      onMessage: (fn) => { messageHandler = fn; return () => {}; },
      reportProgress: async (event) => progress.push(event),
    };
    const running = handler(ctx);
    await new Promise((resolve) => setTimeout(resolve, 10));
    await messageHandler({ content: "Supervisor soft deadline reached. Converge now and emit the mandatory artifact." });
    const result = await running;
    assert.equal(calls.length, 2);
    assert.equal(calls[0].kind, "exploration");
    assert.equal(calls[0].maxToolCalls, 28);
    assert.equal(calls[0].timeoutMs, 180_000);
    assert.equal(calls[1].kind, "finalization");
    assert.equal(calls[1].timeoutMs, 45_000);
    assert.match(calls[1].prompt, /RADAR_CONTEXT_C001 source-run-36f/);
    assert.equal("maxToolCalls" in calls[1], false);
    assert.equal("excludeTools" in calls[1], false);
    assert.match(calls[1].prompt, /FINALIZATION MODE/);
    assert.doesNotMatch(calls[1].prompt, /MUST_RUN_TESTS_AND_SEARCH/);
    assert.match(calls[1].prompt, /Do NOT run tests, searches, git commands/);
    assert.match(calls[1].prompt, /Worker Adapter will persist it/);
    assert.ok(progress.some((event) => event.stage === "forced_finalization"));
    assert.equal(result.artifacts[0].path, "primary.md");
    const fs = await import("node:fs/promises");
    assert.doesNotMatch(await fs.readFile(join(root, "primary.md"), "utf8"), /AI_TEAM_ARTIFACT_STATUS: in_progress/);
  } finally { await rm(root, { recursive: true, force: true }); }
});


test("Qwen text finalization API sends no tools field", async () => {
  const oldKey = process.env.QWEN_API_KEY;
  process.env.QWEN_API_KEY = "test-qwen-key";
  const requests = [];
  try {
    const result = await runQwenTextFinalization({
      task: { workspace: process.cwd() },
      prompt: "final report only",
      timeoutMs: 1_000,
      fetchImpl: async (url, options) => {
        requests.push({ url, options, body: JSON.parse(options.body) });
        return {
          ok: true,
          status: 200,
          async json() { return { choices: [{ message: { content: "# final" } }] }; },
          async text() { return ""; },
        };
      },
    });
    assert.match(result.stdout, /# final/);
    assert.equal(requests.length, 1);
    assert.equal("tools" in requests[0].body, false);
    assert.equal("tool_choice" in requests[0].body, false);
    assert.equal(requests[0].body.messages[0].content, "final report only");
  } finally {
    if (oldKey === undefined) delete process.env.QWEN_API_KEY; else process.env.QWEN_API_KEY = oldKey;
  }
});

test("Qwen finalization exclusion list covers critical exploration tools", () => {
  for (const tool of ["read_file", "run_shell_command", "edit", "write_file", "web_search", "web_fetch", "agent"]) {
    assert.ok(QWEN_FINALIZATION_EXCLUDED_TOOLS.includes(tool), `missing ${tool}`);
  }
});

test("Qwen finalization text is persisted by the adapter without model file tools", async () => {
  const root = await tempWorkspace();
  try {
    const task = {
      task_id: "persist-final",
      title: "Persist final",
      workspace: root,
      prompt: "",
      metadata: { expected_artifacts: ["primary.md"] },
    };
    await initializeRequiredArtifacts(task);
    const relative = await persistFinalizationText(task, "# Final report\n\nNOT VERIFIED: one gap");
    assert.equal(relative, "primary.md");
    const fs = await import("node:fs/promises");
    const content = await fs.readFile(join(root, "primary.md"), "utf8");
    assert.match(content, /^AI_TEAM_ARTIFACT_STATUS: completed/);
    assert.match(content, /NOT VERIFIED: one gap/);
    assert.doesNotMatch(content, /AI_TEAM_ARTIFACT_STATUS: in_progress/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("phase-aware Qwen finalizer preserves review and validation contracts without original exploration prompt", () => {
  const review = finalizePromptFor({
    task_id: "review-finalize", title: "Review", prompt: "RUN_ALL_TESTS_AND_SEARCH",
    metadata: { team_phase: "review", expected_artifacts: ["review.md"] },
  }, []);
  assert.doesNotMatch(review, /RUN_ALL_TESTS_AND_SEARCH/);
  assert.match(review, /## ACCEPTED_CHECKS/);
  assert.match(review, /VERDICT: accept \| repair \| insufficient_evidence/);
  const validation = finalizePromptFor({
    task_id: "validation-finalize", title: "Validate", prompt: "RUN_ALL_TESTS_AND_SEARCH",
    metadata: { team_phase: "validation", expected_artifacts: ["validation_report.md"] },
  }, []);
  assert.match(validation, /## TEST_EVIDENCE/);
  assert.match(validation, /VERDICT: accept \| repair \| blocked/);
});

test("missing required Qwen artifact fails", async () => {
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

test("Qwen refuses an in-progress mandatory artifact as completed output", async () => {
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
