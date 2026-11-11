import test from "node:test";
import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  ARTIFACT_IN_PROGRESS_MARKER,
  buildCodebuddyArgs,
  codebuddyProgressFromEvent,
  collectArtifacts,
  createTaskHandler,
  executionBudgetFor,
  expectedArtifacts,
  finalizePromptFor,
  initializeRequiredArtifacts,
  isCheckpointableArtifact,
  isFinalizeMessage,
  persistFinalizationText,
  promptArtifacts,
  promptFor,
  requiredArtifacts,
  resolveCodebuddyBin,
  resolveCodebuddyModel,
  resolveMaxTurns,
  runCodebuddyPrompt,
} from "../src/index.js";
import {
  BOOTSTRAP_TOKEN_REQUIRED,
  READINESS_TOKEN,
  requireBootstrapToken,
  isReadinessResponse,
  runModelPreflight,
} from "../src/readiness.js";

async function tempWorkspace() {
  return mkdtemp(join(tmpdir(), "workbuddy-worker-"));
}

test("CodeBuddy args use bounded headless mode with positional prompt", () => {
  const previous = { ...process.env };
  delete process.env.CODEBUDDY_MODEL;
  delete process.env.CODEBUDDY_EFFORT;
  delete process.env.WORKBUDDY_USE_WORKTREE;
  try {
    const args = buildCodebuddyArgs({ prompt: "hello", workspace: "/tmp/work", outputFormat: "stream-json" });
    assert.ok(args.includes("-p"));
    assert.ok(args.includes("--output-format"));
    assert.ok(args.includes("stream-json"));
    assert.ok(args.includes("--permission-mode"));
    assert.ok(args.includes("auto"));
    assert.ok(args.includes("--no-session-persistence"));
    assert.ok(args.includes("--max-turns"));
    assert.equal(args.at(-1), "hello");
    assert.equal(args.some((v) => v.includes("--prompt")), false);
  } finally {
    Object.assign(process.env, previous);
  }
});

test("CodeBuddy args include model when configured", () => {
  const previous = process.env.CODEBUDDY_MODEL;
  process.env.CODEBUDDY_MODEL = "glm-5.0";
  try {
    const args = buildCodebuddyArgs({ prompt: "test", workspace: "/tmp" });
    assert.ok(args.includes("--model"));
    assert.ok(args.includes("glm-5.0"));
  } finally {
    if (previous === undefined) delete process.env.CODEBUDDY_MODEL;
    else process.env.CODEBUDDY_MODEL = previous;
  }
});

test("CodeBuddy args include effort when configured", () => {
  const previous = process.env.CODEBUDDY_EFFORT;
  process.env.CODEBUDDY_EFFORT = "high";
  try {
    const args = buildCodebuddyArgs({ prompt: "test", workspace: "/tmp" });
    assert.ok(args.includes("--effort"));
    assert.ok(args.includes("high"));
  } finally {
    if (previous === undefined) delete process.env.CODEBUDDY_EFFORT;
    else process.env.CODEBUDDY_EFFORT = previous;
  }
});

test("CodeBuddy args disable tools for finalization", () => {
  const args = buildCodebuddyArgs({ prompt: "finalize", workspace: "/tmp", disableTools: true });
    assert.ok(args.includes("--tools"));
    assert.ok(args.includes(""));
});

test("CodeBuddy args include worktree when enabled", () => {
  const previous = process.env.WORKBUDDY_USE_WORKTREE;
  process.env.WORKBUDDY_USE_WORKTREE = "1";
  try {
    const args = buildCodebuddyArgs({ prompt: "test", workspace: "/tmp" });
    assert.ok(args.includes("--worktree"));
    assert.ok(args.includes("--worktree-branch"));
  } finally {
    if (previous === undefined) delete process.env.WORKBUDDY_USE_WORKTREE;
    else process.env.WORKBUDDY_USE_WORKTREE = previous;
  }
});

test("CodeBuddy args do not embed secrets in argv", () => {
  const args = buildCodebuddyArgs({ prompt: "hello", workspace: "/tmp" });
  assert.equal(args.some((v) => /sk-|api.?key|secret|token/i.test(v)), false);
});

test("resolveMaxTurns returns configured value or default", () => {
  const previous = process.env.CODEBUDDY_MAX_TURNS;
  process.env.CODEBUDDY_MAX_TURNS = "50";
  assert.equal(resolveMaxTurns(), 50);
  delete process.env.CODEBUDDY_MAX_TURNS;
  assert.equal(resolveMaxTurns(), 100);
  process.env.CODEBUDDY_MAX_TURNS = "invalid";
  assert.equal(resolveMaxTurns(), 100);
  if (previous === undefined) delete process.env.CODEBUDDY_MAX_TURNS;
  else process.env.CODEBUDDY_MAX_TURNS = previous;
});

test("resolveCodebuddyBin returns explicit path or default", () => {
  const previous = process.env.CODEBUDDY_BIN;
  process.env.CODEBUDDY_BIN = "/custom/path/codebuddy";
  assert.equal(resolveCodebuddyBin(), "/custom/path/codebuddy");
  delete process.env.CODEBUDDY_BIN;
  const result = resolveCodebuddyBin();
  assert.ok(typeof result === "string" && result.length > 0);
  if (previous === undefined) delete process.env.CODEBUDDY_BIN;
  else process.env.CODEBUDDY_BIN = previous;
});

test("fake CodeBuddy executable emits stream-json and runs in workspace", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-codebuddy");
  try {
    await writeFile(fake, `#!/usr/bin/env node
const args = process.argv.slice(2);
const promptIdx = args.length - 1;
console.log(JSON.stringify({type:"session_start",model:"fake-codebuddy"}));
console.log(JSON.stringify({type:"tool_use",name:"write_file"}));
console.log(JSON.stringify({type:"result",response:"done"}));
`, "utf8");
    await chmod(fake, 0o755);
    const seen = [];
    const result = await runCodebuddyPrompt({
      task: { task_id: "fake", title: "fake", workspace: root, prompt: "do" },
      prompt: "hello",
      codebuddyBin: fake,
      onEvent: async (event) => seen.push(event),
      timeoutMs: 5_000,
    });
    assert.equal(seen.length, 3);
    assert.match(result.stdout, /session_start/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("CodeBuddy stream events map to progress stages", () => {
  assert.deepEqual(
    codebuddyProgressFromEvent({ type: "session_start", model: "glm-5.0" }),
    { stage: "model_session", message: "CodeBuddy session initialized (glm-5.0)", current: 2, total: 5 },
  );
  assert.deepEqual(
    codebuddyProgressFromEvent({ type: "tool_use", name: "write_file" }),
    { stage: "tool_use", message: "CodeBuddy is using write_file" },
  );
  assert.deepEqual(
    codebuddyProgressFromEvent({ type: "result" }),
    { stage: "finalizing", message: "CodeBuddy produced a final result", current: 4, total: 5 },
  );
  assert.equal(codebuddyProgressFromEvent({ type: "unknown" }), null);
});

test("isFinalizeMessage detects convergence commands", () => {
  assert.ok(isFinalizeMessage("soft deadline reached"));
  assert.ok(isFinalizeMessage("converge/finalize now"));
  assert.ok(isFinalizeMessage("FINALIZE"));
  assert.ok(isFinalizeMessage("mandatory artifact is already present"));
  assert.ok(!isFinalizeMessage("continue exploring"));
  assert.ok(!isFinalizeMessage(""));
});

test("executionBudgetFor extracts timeout and tool limits", () => {
  const task = {
    metadata: {
      execution_budget: {
        exploration: { timeout_seconds: 300, max_tool_calls: 50 },
        finalization: { timeout_seconds: 60 },
      },
    },
  };
  const exploration = executionBudgetFor(task, "exploration");
  assert.equal(exploration.timeoutMs, 300_000);
  assert.equal(exploration.maxToolCalls, 50);
  const finalization = executionBudgetFor(task, "finalization");
  assert.equal(finalization.timeoutMs, 60_000);
  assert.equal(finalization.maxToolCalls, undefined);
});

test("promptFor includes task context and follow-ups", () => {
  const task = {
    task_id: "task_123",
    title: "Test Task",
    prompt: "Do something important",
  };
  const prompt = promptFor(task, [{ content: "Follow up: be careful" }]);
  assert.ok(prompt.includes("task_123"));
  assert.ok(prompt.includes("Test Task"));
  assert.ok(prompt.includes("Do something important"));
  assert.ok(prompt.includes("Follow up: be careful"));
  assert.ok(prompt.includes("AI_TEAM_ARTIFACT_STATUS: in_progress"));
});

test("finalizePromptFor includes finalization rules", () => {
  const task = {
    task_id: "task_123",
    title: "Test Task",
    metadata: { expected_artifacts: ["report.md"] },
  };
  const prompt = finalizePromptFor(task, []);
  assert.ok(prompt.includes("FINALIZATION MODE"));
  assert.ok(prompt.includes("report.md"));
  assert.ok(prompt.includes("Do NOT run tests"));
  assert.ok(prompt.includes("EVIDENCE GAP"));
});

test("artifact discovery uses explicit metadata and prompt paths", () => {
  assert.deepEqual(promptArtifacts("write report/a.md and proof.json"), ["report/a.md", "proof.json"]);
  assert.deepEqual(requiredArtifacts({ metadata: { expected_artifacts: ["proof.txt"] } }), ["proof.txt"]);
  assert.deepEqual(requiredArtifacts({ metadata: {} }), []);
});

test("expectedArtifacts merges required and prompt-discovered paths", () => {
  const task = {
    metadata: { expected_artifacts: ["required.md"] },
    prompt: "Also create extra.json",
  };
  const expected = expectedArtifacts(task);
  assert.ok(expected.includes("required.md"));
  assert.ok(expected.includes("extra.json"));
});

test("isCheckpointableArtifact identifies text-like files", () => {
  assert.ok(isCheckpointableArtifact("report.md"));
  assert.ok(isCheckpointableArtifact("notes.txt"));
  assert.ok(isCheckpointableArtifact("changelog.log"));
  assert.ok(!isCheckpointableArtifact("data.json"));
  assert.ok(!isCheckpointableArtifact("image.png"));
  assert.ok(!isCheckpointableArtifact("binary.exe"));
});

test("initializeRequiredArtifacts creates checkpoint skeletons", async () => {
  const root = await tempWorkspace();
  try {
    const task = {
      task_id: "test_init",
      title: "Test Init",
      workspace: root,
      metadata: { expected_artifacts: ["report.md", "data.json"] },
    };
    const initialized = await initializeRequiredArtifacts(task);
    assert.deepEqual(initialized, ["report.md"]);
    const content = await readFile(join(root, "report.md"), "utf8");
    assert.ok(content.includes(ARTIFACT_IN_PROGRESS_MARKER));
    assert.ok(content.includes("test_init"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("persistFinalizationText writes completed artifact", async () => {
  const root = await tempWorkspace();
  try {
    const task = {
      task_id: "test_persist",
      title: "Test Persist",
      workspace: root,
      metadata: { expected_artifacts: ["report.md"] },
    };
    await initializeRequiredArtifacts(task);
    const result = await persistFinalizationText(task, "# Final Report\n\nFindings here.");
    assert.equal(result, "report.md");
    const content = await readFile(join(root, "report.md"), "utf8");
    assert.ok(content.includes("AI_TEAM_ARTIFACT_STATUS: completed"));
    assert.ok(content.includes("# Final Report"));
    assert.ok(!content.includes(ARTIFACT_IN_PROGRESS_MARKER));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("collectArtifacts validates required artifacts exist", async () => {
  const root = await tempWorkspace();
  try {
    const task = {
      task_id: "test_collect",
      title: "Test Collect",
      workspace: root,
      metadata: { expected_artifacts: ["report.md"] },
      prompt: "Also create extra.json",
    };
    await writeFile(join(root, "report.md"), "# Report\n\nDone.", "utf8");
    await writeFile(join(root, "extra.json"), "{}", "utf8");
    const artifacts = await collectArtifacts(task);
    assert.equal(artifacts.length, 2);
    assert.ok(artifacts.some((a) => a.path === "report.md"));
    assert.ok(artifacts.some((a) => a.path === "extra.json"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("collectArtifacts rejects in_progress checkpoint", async () => {
  const root = await tempWorkspace();
  try {
    const task = {
      task_id: "test_checkpoint",
      title: "Test Checkpoint",
      workspace: root,
      metadata: { expected_artifacts: ["report.md"] },
    };
    await writeFile(join(root, "report.md"), ARTIFACT_IN_PROGRESS_MARKER + "\n\n# Incomplete", "utf8");
    await assert.rejects(
      () => collectArtifacts(task),
      /in-progress checkpoint/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("requireBootstrapToken rejects empty token", () => {
  assert.throws(
    () => requireBootstrapToken(""),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  assert.throws(
    () => requireBootstrapToken(undefined),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
  assert.equal(requireBootstrapToken("valid-token"), "valid-token");
});

test("isReadinessResponse validates token", () => {
  assert.ok(isReadinessResponse(READINESS_TOKEN));
  assert.ok(isReadinessResponse("  WORKBUDDY_MODEL_OK  "));
  assert.ok(!isReadinessResponse("wrong"));
  assert.ok(!isReadinessResponse(""));
});

test("preflight requires exact readiness token", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-codebuddy");
  try {
    await writeFile(fake, `#!/usr/bin/env node
console.log("WRONG_TOKEN");
`, "utf8");
    await chmod(fake, 0o755);
    await assert.rejects(
      () => runModelPreflight({
        runPrompt: async ({ signal }) => {
          const result = await runCodebuddyPrompt({
            task: { task_id: "preflight", title: "preflight", workspace: root, prompt: "test" },
            prompt: "test",
            codebuddyBin: fake,
            signal,
            outputFormat: "text",
            timeoutMs: 5_000,
          });
          return { response: String(result.stdout || "").trim() };
        },
        timeoutMs: 5_000,
      }),
      /readiness failed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("CodeBuddy timeout triggers process termination", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-codebuddy");
  try {
    await writeFile(fake, `#!/usr/bin/env node
setTimeout(() => {}, 10000);
`, "utf8");
    await chmod(fake, 0o755);
    await assert.rejects(
      () => runCodebuddyPrompt({
        task: { task_id: "timeout", title: "timeout", workspace: root, prompt: "test" },
        prompt: "hello",
        codebuddyBin: fake,
        timeoutMs: 500,
      }),
      (error) => error.code === "timeout",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("CodeBuddy nonzero exit exposes error detail", async () => {
  const root = await tempWorkspace();
  const fake = join(root, "fake-codebuddy");
  try {
    await writeFile(fake, `#!/usr/bin/env node
console.error("model overloaded");
process.exit(1);
`, "utf8");
    await chmod(fake, 0o755);
    await assert.rejects(
      () => runCodebuddyPrompt({
        task: { task_id: "fail", title: "fail", workspace: root, prompt: "test" },
        prompt: "hello",
        codebuddyBin: fake,
        timeoutMs: 5_000,
      }),
      /exited with code 1.*model overloaded/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("createTaskHandler returns a function", () => {
  const handler = createTaskHandler();
  assert.equal(typeof handler, "function");
});
