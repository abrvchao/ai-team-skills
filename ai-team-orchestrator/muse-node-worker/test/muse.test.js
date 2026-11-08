import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMuseArgs,
  expectedArtifacts,
  finalTextFromEvents,
  museProgressFromEvent,
  promptFor,
} from "../src/index.js";
import {
  READINESS_TOKEN,
  isReadinessResponse,
  runModelPreflight,
} from "../src/readiness.js";

test("buildMuseArgs keeps sandbox and disables unattended approvals", () => {
  const args = buildMuseArgs({
    prompt: "do the work",
    workspace: "/tmp/work",
    model: "muse-spark-test",
    reasoningEffort: "low",
  });
  assert.deepEqual(args.slice(0, 7), [
    "exec",
    "--workspace", "/tmp/work",
    "--trust-workspace",
    "--disable-approval",
    "--model", "muse-spark-test",
  ]);
  assert.ok(args.includes("--json"));
  assert.ok(args.includes("--max-model-steps"));
  assert.ok(!args.includes("--yolo"));
  assert.ok(!args.includes("--disable-sandbox"));
  assert.equal(args.at(-1), "do the work");
});

test("prompt and artifact discovery preserve task contract", () => {
  const task = {
    task_id: "task_1",
    title: "Muse smoke",
    prompt: "Create result.md and notes.json.",
    metadata: { expected_artifacts: ["result.md"] },
  };
  const prompt = promptFor(task, [{ content: "Also verify it." }]);
  assert.match(prompt, /task_1/);
  assert.match(prompt, /Follow-up 1/);
  assert.deepEqual(expectedArtifacts(task), ["result.md", "notes.json"]);
});

test("Muse JSON events map to progress without assuming one schema", () => {
  assert.deepEqual(
    museProgressFromEvent({ type: "tool_use", tool_name: "write_file" }),
    { stage: "tool_use", message: "Muse is using write_file" },
  );
  assert.equal(
    museProgressFromEvent({ type: "assistant_message", text: "working" }).stage,
    "model_session",
  );
});

test("final text can be extracted from common JSONL shapes", () => {
  assert.equal(
    finalTextFromEvents([{ type: "result", response: "MUSE_MODEL_OK" }]),
    "MUSE_MODEL_OK",
  );
  assert.equal(
    finalTextFromEvents([{ message: { content: "MUSE_MODEL_OK" } }]),
    "MUSE_MODEL_OK",
  );
});

test("readiness requires the exact model token", async () => {
  assert.ok(isReadinessResponse(READINESS_TOKEN));
  assert.ok(!isReadinessResponse("almost"));
  const result = await runModelPreflight({
    runPrompt: async () => ({ response: READINESS_TOKEN }),
  });
  assert.equal(result.token, READINESS_TOKEN);
});
