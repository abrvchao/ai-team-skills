// WorkBuddy Node Worker 单元测试
// 全部为纯逻辑测试：不起真实 CLI、不联网、不写生产文件（仅用系统临时目录）。

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  DEFAULT_MODEL,
  ERROR_CODES,
  PAID_MODELS,
  SUPPORTED_MODELS,
  buildCliArgs,
  buildCliEnv,
  classifyCliError,
  collectArtifacts,
  createTaskHandler,
  extractFinalText,
  extractUsage,
  isCheckpointableArtifact,
  promptFor,
  requiredArtifacts,
  resolveMaxTurns,
  resolveModel,
  resolveTaskTimeoutMs,
  safeJoin,
  workbuddyProgressFromEvent,
} from "../src/index.js";
import {
  BOOTSTRAP_TOKEN_REQUIRED,
  startWorkBuddyWorker,
  waitForModel,
  isReadinessResponse,
  isRetryableReadinessError,
  readinessReason,
  requireBootstrapToken,
  runModelPreflight,
} from "../src/readiness.js";

// ---------------------------------------------------------------------------
// 1. 卡死修复：环境变量剔除
// ---------------------------------------------------------------------------

test("buildCliEnv 必须剔除 SERVER__PORT / SERVER__HOST（卡死根因）", () => {
  const env = buildCliEnv({
    SERVER__PORT: "62721",
    SERVER__HOST: "127.0.0.1",
    NODE_OPTIONS: "--x",
    PATH: "/usr/bin",
    ACC_PRODUCT_CONFIG_PATH: "/tmp/acc.json",
  });
  assert.equal(env.SERVER__PORT, undefined);
  assert.equal(env.SERVER__HOST, undefined);
  assert.equal(env.NODE_OPTIONS, undefined);
  assert.equal(env.PATH, "/usr/bin");
  // 认证相关的变量必须保留（否则 CLI 更不可能通过认证）。
  assert.equal(env.ACC_PRODUCT_CONFIG_PATH, "/tmp/acc.json");
});

test("buildCliEnv 允许注入额外变量且不污染原对象", () => {
  const base = { A: "1" };
  const env = buildCliEnv(base, { NO_COLOR: "1" });
  assert.equal(env.NO_COLOR, "1");
  assert.equal(env.A, "1");
  assert.equal(base.NO_COLOR, undefined);
});

// ---------------------------------------------------------------------------
// 2. CLI 参数
// ---------------------------------------------------------------------------

test("buildCliArgs 生成非交互参数且 prompt 在最后", () => {
  const args = buildCliArgs({
    prompt: "hello",
    model: "glm-5.3",
    permissionMode: "plan",
    outputFormat: "stream-json",
    maxTurns: 7,
    addDirs: ["/tmp/ws"],
    tools: "",
  });
  assert.ok(args.includes("--print"));
  assert.equal(args[args.indexOf("--model") + 1], "glm-5.3");
  assert.equal(args[args.indexOf("--permission-mode") + 1], "plan");
  assert.equal(args[args.indexOf("--output-format") + 1], "stream-json");
  assert.equal(args[args.indexOf("--max-turns") + 1], "7");
  assert.equal(args[args.indexOf("--add-dir") + 1], "/tmp/ws");
  assert.equal(args[args.indexOf("--tools") + 1], "");
  assert.equal(args[args.length - 1], "hello");
});

test("buildCliArgs 在未给 maxTurns 时不带轮次上限参数", () => {
  const args = buildCliArgs({ prompt: "x", maxTurns: undefined });
  assert.equal(args.includes("--max-turns"), false);
});

// ---------------------------------------------------------------------------
// 3. 模型选择与付费闸门
// ---------------------------------------------------------------------------

test("resolveModel 默认使用低风险模型", () => {
  assert.equal(resolveModel({ task: {}, env: {} }), DEFAULT_MODEL);
});

test("resolveModel 优先使用任务元数据指定的模型", () => {
  const model = resolveModel({ task: { metadata: { model: "kimi-k3-2" } }, env: {} });
  assert.equal(model, "kimi-k3-2");
});

test("resolveModel 拒绝不在实测清单里的模型", () => {
  assert.throws(
    () => resolveModel({ task: { metadata: { model: "not-a-real-model" } }, env: {} }),
    (error) => error.code === ERROR_CODES.MODEL_NOT_ALLOWED,
  );
});

test("resolveModel 默认拦截付费模型", () => {
  const paid = PAID_MODELS[0];
  assert.throws(
    () => resolveModel({ task: { metadata: { model: paid } }, env: {} }),
    (error) => error.code === ERROR_CODES.PAID_MODEL_BLOCKED,
  );
});

test("resolveModel 显式放行后可选择付费模型", () => {
  const paid = PAID_MODELS[0];
  const model = resolveModel({
    task: { metadata: { model: paid } },
    env: { WORKBUDDY_ALLOW_PAID_MODELS: "1" },
  });
  assert.equal(model, paid);
});

test("SUPPORTED_MODELS 覆盖任务书点名的国产模型家族", () => {
  const joined = SUPPORTED_MODELS.join(",");
  for (const family of ["deepseek", "glm", "kimi", "minimax", "step", "hy"]) {
    assert.ok(joined.includes(family), `缺少模型家族: ${family}`);
  }
});

// ---------------------------------------------------------------------------
// 4. 错误分类
// ---------------------------------------------------------------------------

test("classifyCliError 识别未认证", () => {
  const result = classifyCliError({
    stdout: "Authentication required. Please use /login command to sign in to your account",
  });
  assert.equal(result.code, ERROR_CODES.AUTH);
  assert.equal(result.retryable, false);
});

test("classifyCliError 识别限流并允许重试", () => {
  const result = classifyCliError({ stderr: "429 too many requests" });
  assert.equal(result.code, ERROR_CODES.RATE_LIMIT);
  assert.equal(result.retryable, true);
});

test("classifyCliError 识别取消（AbortError）", () => {
  const error = new Error("x");
  error.name = "AbortError";
  assert.equal(classifyCliError({ error }).code, ERROR_CODES.CANCELLED);
});

test("classifyCliError 识别 CLI 不存在", () => {
  const error = new Error("spawn ENOENT");
  error.code = "ENOENT";
  assert.equal(classifyCliError({ error }).code, ERROR_CODES.CLI_MISSING);
});

test("classifyCliError 对普通非零退出归类为 cli_exit", () => {
  assert.equal(classifyCliError({ exitCode: 3 }).code, ERROR_CODES.CLI_EXIT);
});

// ---------------------------------------------------------------------------
// 5. 输出与用量解析
// ---------------------------------------------------------------------------

test("extractFinalText 优先使用 result 事件", () => {
  const text = extractFinalText({
    events: [
      { type: "assistant", message: { content: [{ type: "text", text: "partial" }] } },
      { type: "result", result: "final body" },
    ],
  });
  assert.equal(text, "final body");
});

test("extractFinalText 不得把 CLI 原始事件与 prompt 回显当成模型回复", () => {
  assert.equal(extractFinalText({
    events: [
      { type: "system", subtype: "init" },
      { type: "result", is_error: false, result: " " },
    ],
    raw: "You are executing AI Team task\nnot found\n{\"type\":\"result\",\"result\":\" \"}",
  }), "");
});

test("WorkBuddy handler 禁止传变长 --add-dir 导致 prompt 变路径", async () => {
  const root = await mkdtemp(join(tmpdir(), "workbuddy-dir-"));
  let seen = false;
  try {
    const handler = createTaskHandler({
      runPrompt: async (opts) => {
        seen = true;
        assert.deepEqual(opts.addDirs, []);
        assert.equal(opts.task.workspace, root);
        return { text: "STANDALONE_WORKER_OK", usage: { known: false } };
      },
    });
    const task = {
      task_id: "dir-check", workspace: root, title: "Directory argument regression",
      prompt: "x", metadata: { expected_artifacts: ["proof.txt"] },
    };
    const result = await handler({
      task, getMessages: () => [], refreshMessages: async () => [],
      onMessage: () => () => {}, reportProgress: async () => {},
    });
    assert.equal(seen, true);
    assert.equal(result.artifacts[0].path, "proof.txt");
    assert.match(await readFile(join(root, "proof.txt"), "utf8"), /STANDALONE_WORKER_OK/);
  } finally {
    const { rm } = await import("node:fs/promises");
    await rm(root, { recursive: true, force: true });
  }
});

test("extractFinalText 在无 result 事件时累积 assistant 文本", () => {
  const text = extractFinalText({
    events: [
      { type: "assistant", message: { content: [{ type: "text", text: "A" }] } },
      { type: "assistant", message: { content: [{ type: "text", text: "B" }] } },
    ],
  });
  assert.equal(text, "AB");
});

test("extractUsage 取不到用量时明确标记 unknown", () => {
  const usage = extractUsage({ events: [{ type: "assistant", message: { content: [] } }] });
  assert.equal(usage.known, false);
  assert.equal(usage.note, "usage_unknown_by_cli");
  assert.equal(usage.totalTokens, null);
});

test("extractUsage 汇总 token 与成本", () => {
  const usage = extractUsage({
    events: [
      { type: "assistant", usage: { input_tokens: 10, output_tokens: 5 } },
      { type: "assistant", usage: { input_tokens: 1, output_tokens: 2 } },
      { type: "result", total_cost_usd: 0.02 },
    ],
  });
  assert.equal(usage.known, true);
  assert.equal(usage.turns, 2);
  assert.equal(usage.inputTokens, 11);
  assert.equal(usage.outputTokens, 7);
  assert.equal(usage.costUsd, 0.02);
});

test("workbuddyProgressFromEvent 映射会话初始化与工具调用", () => {
  assert.equal(
    workbuddyProgressFromEvent({ type: "system", subtype: "init", model: "glm-5.3" }).stage,
    "model_session",
  );
  assert.equal(
    workbuddyProgressFromEvent({
      type: "assistant",
      message: { content: [{ type: "tool_use", name: "Read" }] },
    }).stage,
    "tool_use",
  );
  assert.equal(workbuddyProgressFromEvent({ type: "unknown" }), null);
});

// ---------------------------------------------------------------------------
// 6. 工作区与路径安全
// ---------------------------------------------------------------------------

test("safeJoin 允许工作区内相对路径", () => {
  assert.equal(safeJoin("/tmp/ws", "sub/report.md"), "/tmp/ws/sub/report.md");
});

test("safeJoin 拒绝路径穿越", () => {
  assert.throws(
    () => safeJoin("/tmp/ws", "../escape.md"),
    (error) => error.code === ERROR_CODES.WORKSPACE_ESCAPE,
  );
});

test("safeJoin 拒绝绝对路径", () => {
  assert.throws(
    () => safeJoin("/tmp/ws", "/etc/passwd"),
    (error) => error.code === ERROR_CODES.WORKSPACE_ESCAPE,
  );
});

test("isCheckpointableArtifact 只认文本类产物", () => {
  assert.equal(isCheckpointableArtifact("report.md"), true);
  assert.equal(isCheckpointableArtifact("data.csv"), true);
  assert.equal(isCheckpointableArtifact("image.png"), false);
});

// ---------------------------------------------------------------------------
// 7. Prompt 构造
// ---------------------------------------------------------------------------

test("promptFor 带上任务标题、正文与后续消息", () => {
  const prompt = promptFor(
    { task_id: "t1", title: "Review docs", prompt: "Do the thing" },
    [{ content: "please hurry" }],
  );
  assert.ok(prompt.includes("t1"));
  assert.ok(prompt.includes("Review docs"));
  assert.ok(prompt.includes("Do the thing"));
  assert.ok(prompt.includes("please hurry"));
});

test("requiredArtifacts 去重且忽略空值", () => {
  const artifacts = requiredArtifacts({
    metadata: { expected_artifacts: ["a.md", "", "a.md", "b.md"] },
  });
  assert.deepEqual(artifacts, ["a.md", "b.md"]);
});

// ---------------------------------------------------------------------------
// 8. 时长 / 轮次配置
// ---------------------------------------------------------------------------

test("resolveTaskTimeoutMs 回落到默认值", () => {
  assert.ok(resolveTaskTimeoutMs({}) > 0);
  assert.equal(resolveTaskTimeoutMs({ WORKBUDDY_TASK_TIMEOUT_MS: "5000" }), 5000);
  assert.ok(resolveTaskTimeoutMs({ WORKBUDDY_TASK_TIMEOUT_MS: "-1" }) > 0);
});

test("resolveMaxTurns 回落到默认值", () => {
  assert.ok(resolveMaxTurns({}) > 0);
  assert.equal(resolveMaxTurns({ WORKBUDDY_MAX_TURNS: "12" }), 12);
  assert.ok(resolveMaxTurns({ WORKBUDDY_MAX_TURNS: "abc" }) > 0);
});

// ---------------------------------------------------------------------------
// 9. 就绪模块
// ---------------------------------------------------------------------------

test("requireBootstrapToken 缺失时抛 bootstrap_token_required", () => {
  assert.throws(
    () => requireBootstrapToken(""),
    (error) => error.code === BOOTSTRAP_TOKEN_REQUIRED,
  );
});

test("isReadinessResponse 严格全等", () => {
  assert.equal(isReadinessResponse("WORKBUDDY_MODEL_OK"), true);
  assert.equal(isReadinessResponse(" WORKBUDDY_MODEL_OK "), true);
  assert.equal(isReadinessResponse("WORKBUDDY_MODEL_OK\nnote"), false);
});

test("runModelPreflight 通过时返回 token", async () => {
  const result = await runModelPreflight({
    runPrompt: async () => ({ response: "WORKBUDDY_MODEL_OK" }),
  });
  assert.equal(result.token, "WORKBUDDY_MODEL_OK");
});

test("runModelPreflight 答错时报 readiness_token_mismatch", async () => {
  await assert.rejects(
    () => runModelPreflight({ runPrompt: async () => ({ response: "sure!" }) }),
    (error) => error.code === "readiness_token_mismatch",
  );
});

test("认证失败不可重试，其余可重试", () => {
  assert.equal(isRetryableReadinessError({ code: ERROR_CODES.AUTH }), false);
  assert.equal(isRetryableReadinessError({ code: BOOTSTRAP_TOKEN_REQUIRED }), false);
  assert.equal(isRetryableReadinessError({ code: ERROR_CODES.MODEL_UNAVAILABLE }), true);
  assert.equal(readinessReason({ code: ERROR_CODES.AUTH }), ERROR_CODES.AUTH);
});

// 仅使用模拟模型与 Bridge：覆盖底层和公开启动入口的重试参数绕过。
test("waitForModel 即使传 maxAttempts=3 也只预检一次", async () => {
  let calls = 0;
  const result = await waitForModel({
    bootstrapToken: "offline-fake-token",
    maxAttempts: 3,
    client: { reportReadiness: async () => {} },
    preflight: async () => { calls += 1; throw new Error("offline preflight failed"); },
    sleepImpl: async () => { throw new Error("must not retry"); },
    logger: { error: () => {} },
  });
  assert.equal(calls, 1);
  assert.equal(result.attempts, 1);
  assert.equal(result.ready, false);
});

test("startWorkBuddyWorker 即使传 maxAttempts=3 也只预检一次", async () => {
  let calls = 0;
  let started = false;
  const result = await startWorkBuddyWorker({
    bootstrapToken: "offline-fake-token",
    maxAttempts: 3,
    client: { reportReadiness: async () => {} },
    preflight: async () => { calls += 1; throw new Error("offline preflight failed"); },
    sleepImpl: async () => { throw new Error("must not retry"); },
    workerFactory: () => { started = true; throw new Error("must not start"); },
  });
  assert.equal(calls, 1);
  assert.equal(result.readiness.attempts, 1);
  assert.equal(result.started, false);
  assert.equal(started, false);
});

// ---------------------------------------------------------------------------
// 10. 任务处理器（端到端逻辑，使用替身 runPrompt）
// ---------------------------------------------------------------------------

function makeCtx(task, { onMessage } = {}) {
  const progress = [];
  const stubbed = {
    task,
    signal: new AbortController().signal,
    getMessages: () => [],
    refreshMessages: async () => [],
    onMessage: (handler) => {
      onMessage?.(handler);
      return () => {};
    },
    reportProgress: async (event) => {
      progress.push(event);
    },
  };
  return { ctx: stubbed, progress };
}

test("handleTask 落盘产物、登记 artifact 并上报进度", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "wb-worker-"));
  const task = {
    task_id: "task-1",
    title: "Write a short report",
    prompt: "Write it",
    workspace,
    metadata: { expected_artifacts: ["report.md"] },
  };
  const calls = [];
  const handleTask = createTaskHandler({
    runPrompt: async (options) => {
      calls.push(options);
      return {
        text: "# Report\n\nAll good.\n",
        usage: { known: true, turns: 1, inputTokens: 3, outputTokens: 4, costUsd: null },
        model: options.model,
      };
    },
  });
  const { ctx, progress } = makeCtx(task);
  const result = await handleTask(ctx);

  const written = await readFile(join(workspace, "report.md"), "utf8");
  assert.ok(written.includes("AI_TEAM_ARTIFACT_STATUS: completed"));
  assert.ok(written.includes("All good."));
  assert.deepEqual(result.artifacts.map((a) => a.path), ["report.md"]);
  assert.equal(calls.length, 1);
  assert.ok(progress.some((p) => p.stage === "artifact_initialized"));
  assert.ok(progress.some((p) => p.stage === "artifacts"));
  assert.ok(progress.some((p) => p.stage === "usage"));
});

test("handleTask 在产物仍为 checkpoint 时失败，不假称完成", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "wb-worker-"));
  const task = {
    task_id: "task-2",
    title: "Needs an artifact",
    prompt: "Do it",
    workspace,
    metadata: { expected_artifacts: ["out.md"] },
  };
  // 模型什么都没写，产物停留在 in_progress checkpoint → 必须失败。
  const handleTask = createTaskHandler({
    runPrompt: async () => ({ text: "", usage: { known: false }, model: "deepseek-v4.1-flash" }),
  });
  const { ctx } = makeCtx(task);
  await assert.rejects(() => handleTask(ctx), (error) => error.code === ERROR_CODES.ARTIFACT_INCOMPLETE);
});

test("handleTask 拒绝不存在的工作区", async () => {
  const task = {
    task_id: "task-3",
    title: "Bad workspace",
    prompt: "x",
    workspace: "/definitely/not/here",
    metadata: {},
  };
  const handleTask = createTaskHandler({ runPrompt: async () => ({ text: "x" }) });
  const { ctx } = makeCtx(task);
  await assert.rejects(() => handleTask(ctx), (error) => error.code === ERROR_CODES.WORKSPACE_INVALID);
});

test("collectArtifacts 对已存在的非 checkpoint 产物返回登记信息", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "wb-worker-"));
  await writeFile(join(workspace, "done.md"), "AI_TEAM_ARTIFACT_STATUS: completed\n\nbody\n", "utf8");
  const artifacts = await collectArtifacts({
    workspace,
    metadata: { expected_artifacts: ["done.md"] },
  });
  assert.deepEqual(artifacts.map((a) => a.path), ["done.md"]);
});
