import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  ARTIFACT_IN_PROGRESS_MARKER,
  createTaskHandler,
  expectedArtifacts,
  finalizePromptFor,
  initializeRequiredArtifacts,
  promptArtifacts,
  reportArtifacts,
  requiredArtifacts,
} from "../src/index.js";

async function workspaceWith(files) {
  const root = await mkdtemp(join(tmpdir(), "dsh-artifacts-"));
  for (const file of files) {
    const target = join(root, file);
    await mkdir(join(target, ".."), { recursive: true });
    await writeFile(target, "x", "utf8");
  }
  return root;
}

function collector() {
  const reported = [];
  return {
    reported,
    addArtifact: async (artifact) => {
      reported.push(artifact.path);
      return artifact;
    },
  };
}

test("prompt-named files are extracted from a mixed-language prompt", () => {
  const prompt =
    "在当前工作目录生成 dsh-main-online-proof.txt，写入 DSH main online 和当前时间，并把该文件作为 artifact 返回。";
  assert.deepEqual(promptArtifacts(prompt), ["dsh-main-online-proof.txt"]);
});

test("prompt artifacts dedupe, strip ./ and keep relative paths", () => {
  const prompt = "write ./out/report.json then out/report.json and summary.md";
  assert.deepEqual(promptArtifacts(prompt), ["out/report.json", "summary.md"]);
});

test("prompt artifacts ignore prose that is not a file", () => {
  assert.deepEqual(promptArtifacts("just explain the design, no files"), []);
});

test("required artifacts follow metadata and the known logical task", () => {
  assert.deepEqual(
    requiredArtifacts({ task_id: "t1", prompt: "", metadata: {} }),
    [],
  );
  assert.deepEqual(
    requiredArtifacts({
      task_id: "t2",
      prompt: "",
      metadata: { expected_artifacts: ["out/a.json"] },
    }),
    ["out/a.json"],
  );
  assert.deepEqual(
    requiredArtifacts({
      task_id: "t3",
      prompt: "",
      metadata: { logical_task_id: "CN-DSH-001" },
    }),
    ["docs/research/china-social/zhihu-acquisition.md"],
  );
});

test("expected artifacts merge required and prompt candidates without duplicates", () => {
  const task = {
    task_id: "t4",
    prompt: "produce out/a.json and proof.txt",
    metadata: { expected_artifacts: ["out/a.json"] },
  };
  assert.deepEqual(expectedArtifacts(task), ["out/a.json", "proof.txt"]);
});

test("regression: the post-merge proof filename is discovered", () => {
  const task = {
    task_id: "t5",
    prompt: "create dsh-main-online-proof.txt and return it as artifact",
    metadata: {},
  };
  assert.deepEqual(expectedArtifacts(task), ["dsh-main-online-proof.txt"]);
});


test("mandatory markdown artifact checkpoint is created early without overwriting real work", async () => {
  const root = await workspaceWith([]);
  try {
    const task = { task_id: "checkpoint", title: "Checkpoint", workspace: root, prompt: "", metadata: { expected_artifacts: ["primary.md", "data.json"] } };
    const initialized = await initializeRequiredArtifacts(task);
    assert.deepEqual(initialized, ["primary.md"]);
    const first = await (await import("node:fs/promises")).readFile(join(root, "primary.md"), "utf8");
    assert.match(first, new RegExp(ARTIFACT_IN_PROGRESS_MARKER));
    await writeFile(join(root, "primary.md"), "# real artifact\n", "utf8");
    const second = await initializeRequiredArtifacts(task);
    assert.deepEqual(second, []);
    const preserved = await (await import("node:fs/promises")).readFile(join(root, "primary.md"), "utf8");
    assert.equal(preserved, "# real artifact\n");
  } finally { await rm(root, { recursive: true, force: true }); }
});


test("Supervisor finalize interrupts active DSH exploration and runs bounded finalization", async () => {
  const root = await workspaceWith([]);
  try {
    let messageHandler;
    const progress = [];
    const calls = [];
    const handler = createTaskHandler({
      runPrompt: async ({ signal, prompt, timeoutMs, maxAttempts }) => {
        calls.push({ prompt, timeoutMs, maxAttempts });
        if (calls.length === 1) {
          await new Promise((resolve, reject) => {
            if (signal.aborted) {
              const error = new Error("aborted"); error.name = "AbortError"; reject(error); return;
            }
            signal.addEventListener("abort", () => {
              const error = new Error("aborted"); error.name = "AbortError"; reject(error);
            }, { once: true });
          });
          return { stdout: "", stderr: "" };
        }
        await writeFile(join(root, "primary.md"), "# decision ready\n", "utf8");
        return { stdout: "", stderr: "" };
      },
    });
    const reported = [];
    const ctx = {
      task: { task_id: "finalize-dsh", title: "Finalize DSH", workspace: root, prompt: "MUST_RUN_TESTS_AND_SEARCH before Create primary.md", metadata: {
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
      addArtifact: async (artifact) => { reported.push(artifact); return artifact; },
    };
    const running = handler(ctx);
    await new Promise((resolve) => setTimeout(resolve, 10));
    await messageHandler({ content: "Supervisor soft deadline reached. Converge now and emit the mandatory artifact." });
    const result = await running;
    assert.equal(calls.length, 2);
    assert.equal(calls[0].timeoutMs, 180_000);
    assert.equal(calls[1].timeoutMs, 45_000);
    assert.match(calls[1].prompt, /RADAR_CONTEXT_C001 source-run-36f/);
    assert.equal(calls[1].maxAttempts, 1);
    assert.match(calls[1].prompt, /FINALIZATION MODE/);
    assert.doesNotMatch(calls[1].prompt, /MUST_RUN_TESTS_AND_SEARCH/);
    assert.match(calls[1].prompt, /Do NOT run tests, searches, git commands/);
    assert.ok(progress.some((event) => event.stage === "forced_finalization"));
    assert.equal(result.artifacts[0].path, "primary.md");
    assert.equal(reported[0].path, "primary.md");
    const fs = await import("node:fs/promises");
    assert.doesNotMatch(await fs.readFile(join(root, "primary.md"), "utf8"), /AI_TEAM_ARTIFACT_STATUS: in_progress/);
  } finally { await rm(root, { recursive: true, force: true }); }
});


test("phase-aware DSH finalizer preserves synthesis and implementation contracts without original exploration prompt", () => {
  const synthesis = finalizePromptFor({
    task_id: "synth-finalize", title: "Synthesis", prompt: "RUN_ALL_TESTS_AND_SEARCH",
    metadata: { team_phase: "synthesis", expected_artifacts: ["final.md"] },
  }, []);
  assert.doesNotMatch(synthesis, /RUN_ALL_TESTS_AND_SEARCH/);
  assert.match(synthesis, /inputs\/\*\.md/);
  assert.match(synthesis, /coverage gaps/);
  const implementation = finalizePromptFor({
    task_id: "impl-finalize", title: "Implementation", prompt: "RUN_ALL_TESTS_AND_SEARCH",
    metadata: { team_phase: "implementation", expected_artifacts: ["implementation_report.md"] },
  }, []);
  assert.match(implementation, /IMPLEMENTATION_STATUS: changed \| no_change \| blocked/);
  assert.match(implementation, /## TEST_RESULTS/);
});

test("a produced prompt-named file is reported as an artifact", async () => {
  const root = await workspaceWith(["dsh-main-online-proof.txt"]);
  try {
    const sink = collector();
    const artifacts = await reportArtifacts({
      task: {
        task_id: "t6",
        workspace: root,
        prompt: "create dsh-main-online-proof.txt and return it as artifact",
        metadata: {},
      },
      addArtifact: sink.addArtifact,
    });
    assert.deepEqual(sink.reported, ["dsh-main-online-proof.txt"]);
    assert.deepEqual(artifacts, [
      { path: "dsh-main-online-proof.txt", kind: "file", label: "dsh-main-online-proof.txt" },
    ]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a prompt-named file that was not produced is skipped, not failed", async () => {
  const root = await workspaceWith([]);
  try {
    const sink = collector();
    const artifacts = await reportArtifacts({
      task: {
        task_id: "t7",
        workspace: root,
        prompt: "create missing.txt",
        metadata: {},
      },
      addArtifact: sink.addArtifact,
    });
    assert.deepEqual(sink.reported, []);
    assert.deepEqual(artifacts, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a missing required artifact fails the task", async () => {
  const root = await workspaceWith([]);
  try {
    const sink = collector();
    await assert.rejects(
      () =>
        reportArtifacts({
          task: {
            task_id: "t8",
            workspace: root,
            prompt: "",
            metadata: { expected_artifacts: ["must-exist.json"] },
          },
          addArtifact: sink.addArtifact,
        }),
      /Expected artifact was not created: must-exist\.json/,
    );
    assert.deepEqual(sink.reported, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("nested prompt paths are reported relative to the workspace", async () => {
  const root = await workspaceWith(["docs/research/note.md"]);
  try {
    const sink = collector();
    await reportArtifacts({
      task: {
        task_id: "t9",
        workspace: root,
        prompt: "write docs/research/note.md",
        metadata: {},
      },
      addArtifact: sink.addArtifact,
    });
    assert.deepEqual(sink.reported, ["docs/research/note.md"]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("DSH refuses an in-progress mandatory artifact as completed output", async () => {
  const root = await workspaceWith([]);
  try {
    await writeFile(join(root, "primary.md"), "AI_TEAM_ARTIFACT_STATUS: in_progress\n# checkpoint\n", "utf8");
    await assert.rejects(
      () => reportArtifacts({
        task: { workspace: root, prompt: "", metadata: { expected_artifacts: ["primary.md"] } },
        addArtifact: async () => {},
      }),
      /still an in-progress checkpoint/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
