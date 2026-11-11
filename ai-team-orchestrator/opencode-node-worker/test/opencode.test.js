import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildArgs, parseEvents, extractText, artifactName, persistArtifact,
  preflight, handleTask,
} from "../src/index.js";

test("免费模型白名单与 plan/pure 防止代码执行权限", () => {
  const args = buildArgs("hello");
  assert.ok(args.includes("--pure"));
  assert.equal(args[args.indexOf("--agent") + 1], "plan");
  assert.equal(args[args.indexOf("--model") + 1], "opencode/mimo-v2.6-flash-free");
  assert.ok(!args.includes("--auto"));
  assert.throws(() => buildArgs("x", { model: "paid-model" }), /not_allowlisted/);
});

test("JSON text 事件是唯一正文来源，禁止日志假充结果", () => {
  const parsed = parseEvents([
    "Some plain text not from model",
    JSON.stringify({ type: "step_start", part: { type: "step-start" } }),
    JSON.stringify({ type: "text", part: { type: "text", text: "MODEL_OK" } }),
    JSON.stringify({ type: "step_finish", part: { type: "step-finish" } }),
  ].join("\n"));
  assert.deepEqual(parsed.map((x) => x.type), ["step_start", "text", "step_finish"]);
  assert.equal(extractText(parsed), "MODEL_OK");
  assert.equal(extractText([{ type: "step_finish", part: { text: "not content" } }]), "");
});

test("只允许只读阶段与工作区相对的 Markdown/TXT", () => {
  assert.equal(artifactName({ metadata: { team_phase: "primary" } }), "primary.md");
  for (const name of ["../escape.md", "/tmp/unsafe.txt", "script.sh"]) {
    assert.throws(() => artifactName({ metadata: { expected_artifacts: [name] } }));
  }
  assert.throws(() => artifactName({ metadata: { team_phase: "implementation" } }),
    /read_only_phase/);
});

test("模型预检必须精确回复，错误不得算在线", async () => {
  await assert.rejects(() => preflight({
    run: async () => ({ text: "wrong" }),
  }), /preflight_mismatch/);
  assert.equal(await preflight({
    run: async () => ({ text: "OPENCODE_WORKER_OK" }),
  }), true);
});

test("隔离产物写入：不覆盖、不穿越 symlink", async () => {
  const root = await mkdtemp(join(tmpdir(), "opencode-worker-"));
  const outside = await mkdtemp(join(tmpdir(), "opencode-outside-"));
  try {
    const task = { workspace: root, metadata: { expected_artifacts: ["checks.md"] } };
    assert.equal(await persistArtifact(task, "## Verification"), "checks.md");
    assert.equal((await readFile(join(root, "checks.md"), "utf8")).trim(), "## Verification");
    await assert.rejects(() => persistArtifact(task, "overwrite"), /EEXIST/);
    await symlink(outside, join(root, "outside-link"));
    await assert.rejects(() => persistArtifact({
      workspace: root, metadata: { expected_artifacts: ["outside-link/escape.md"] },
    }, "hello"), /symlink_escape/);
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(outside, { recursive: true, force: true });
  }
});

test("任务处理器生成可登记的 Markdown 产物和进度事件", async () => {
  const root = await mkdtemp(join(tmpdir(), "opencode-review-"));
  try {
    const progresses = [];
    const reply = await handleTask({
      task: { task_id: "test", title: "Review", prompt: "Produce a review",
        workspace: root,
        metadata: { team_phase: "review", expected_artifacts: ["review.md"] } },
      getMessages: () => [],
      reportProgress: async (step) => progresses.push(step),
    }, { run: async () => ({ text: "## ACCEPTED_CHECKS\n- Supported\nVERDICT: accept" }) });
    assert.equal(reply.artifacts[0].path, "review.md");
    assert.equal(progresses.length, 3);
    assert.match(await readFile(join(root, "review.md"), "utf8"), /VERDICT: accept/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
