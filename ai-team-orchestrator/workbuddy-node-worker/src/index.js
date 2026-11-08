// WorkBuddy Node Worker
// ====================
// 让 AI Team Supervisor 能真正派工给 WorkBuddy。
//
// 设计要点（对应任务书「四、工程实现要求」）：
//   1. Worker 注册与心跳        → 复用 node-worker 的 AgentWorker
//   2. 真实模型调用预检          → 见 ./readiness.js（必须真答对才上线）
//   3. Supervisor 领取任务       → AgentWorker.lease（pull 模式）
//   4. 工作区隔离               → 每次执行前校验 workspace，禁止越界
//   5. 模型选择与执行            → --model + 允许清单 + 付费模型闸门
//   6. 阶段进度回传              → stream-json 事件 → Progress Event
//   7. 最终产物写入与登记        → 先建 checkpoint，收尾由 Adapter 落盘，再校验
//   8. 任务取消与超时处理        → AbortSignal + 硬超时（SIGTERM → SIGKILL）
//   9. 错误分类                 → classifyCliError()：auth / rate_limit / timeout / unavailable / ...
//  10. 用量记录                 → turns / usage / cost；取不到时显式标记为 unknown
//
// 安全与成本（对应任务书「成本和安全要求」）：
//   - 默认 permission-mode=plan（只读），不放开本机写操作；
//   - 默认禁用付费模型自动执行（见 PAID_MODELS + WORKBUDDY_ALLOW_PAID_MODELS）；
//   - 单任务有轮次上限（--max-turns）与墙钟超时；
//   - 认证失败不重试；
//   - 任何日志/产物都不打印凭据与环境变量。

import { spawn } from "node:child_process";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { isAbsolute, resolve, sep } from "node:path";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import { assertArtifactReady, ARTIFACT_IN_PROGRESS_MARKER } from "../../node-worker/src/artifact_truth.js";
import {
  AUTH_UNAVAILABLE,
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  MODEL_UNAVAILABLE,
  RATE_LIMITED,
  READINESS_PROMPT,
  requireBootstrapToken,
  startWorkBuddyWorker,
} from "./readiness.js";

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

/** WorkBuddy 桌面版自带的 CLI 入口（默认值，可用 WORKBUDDY_CLI 覆盖）。 */
export const DEFAULT_CLI =
  "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy";

/** 单任务默认墙钟超时：30 分钟。 */
export const DEFAULT_TASK_TIMEOUT_MS = 1_800_000;
/** 预检默认超时。 */
export const DEFAULT_PREFLIGHT_TIMEOUT_MS_EXPORT = DEFAULT_PREFLIGHT_TIMEOUT_MS;
/** 单任务默认最大 agent 轮次（成本闸门之一）。 */
export const DEFAULT_MAX_TURNS = 30;
/** 默认使用只读计划模式，避免未授权写本机。 */
export const DEFAULT_PERMISSION_MODE = "plan";
/** 默认模型：便宜的 flash 档，够跑预检与一般研究任务。 */
export const DEFAULT_MODEL = "deepseek-v4.1-flash";

/**
 * CLI 实测支持的模型（来源：`codebuddy --help`，v2.156.0，2026-10-11）。
 * 注意：不同账号可能不同；新增模型请同步更新 WORKBUDDY_MODEL_MATRIX.md。
 */
export const SUPPORTED_MODELS = Object.freeze([
  "fast-model",
  "balanced-model",
  "deep-model",
  "deepseek-v4.1-flash",
  "deepseek-v4-pro",
  "glm-5.3",
  "glm-5.3-flash",
  "glm-5.3-flashx",
  "glm-5.2",
  "glm-5.1",
  "glm-5v-turbo",
  "kimi-k3-2",
  "kimi-k2.8-preview",
  "kimi-k2.7",
  "kimi-k2.6",
  "minimax-m3-pay",
  "step-5-preview",
  "hy4-preview",
  "hy3",
  "hy3-x",
  "space-bunny",
]);

/**
 * 明确带「付费」语义的模型。默认禁止自动批量执行。
 * 依据：模型 ID 中的 `-pay` 后缀为官方命名约定（minimax-m3-pay）。
 * 其余模型的计费方式未经账号实测确认，一律按「未知」处理并记录，不做假设。
 */
export const PAID_MODELS = Object.freeze(["minimax-m3-pay"]);

/** 默认放行的「低风险」模型（非 -pay 语义）。 */
export const DEFAULT_SAFE_MODELS = Object.freeze([
  "fast-model",
  "balanced-model",
  "deep-model",
  "deepseek-v4.1-flash",
]);

export const ERROR_CODES = Object.freeze({
  AUTH: AUTH_UNAVAILABLE,
  RATE_LIMIT: RATE_LIMITED,
  TIMEOUT: "timeout",
  CANCELLED: "cancelled",
  MODEL_UNAVAILABLE: MODEL_UNAVAILABLE,
  CLI_MISSING: "cli_missing",
  CLI_EXIT: "cli_exit",
  EMPTY_OUTPUT: "empty_output",
  WORKSPACE_INVALID: "workspace_invalid",
  WORKSPACE_ESCAPE: "workspace_escape",
  MODEL_NOT_ALLOWED: "model_not_allowed",
  PAID_MODEL_BLOCKED: "paid_model_blocked",
  ARTIFACT_INCOMPLETE: "artifact_incomplete",
});

// ---------------------------------------------------------------------------
// 配置解析
// ---------------------------------------------------------------------------

export function resolveCliBin(env = process.env) {
  return (env.WORKBUDDY_CLI || env.WORKBUDDY_CLI_BIN || DEFAULT_CLI).trim();
}

/**
 * CLI 是一个带 `#!/usr/bin/env node` 的 JS 文件。
 * 为了不依赖用户 PATH，默认用「当前 Worker 的 node 可执行文件」去跑它。
 */
export function resolveNodeBin(env = process.env) {
  return (env.WORKBUDDY_NODE_BIN || process.execPath || "node").trim();
}

export function resolveBridgeUrl(env = process.env) {
  return (env.AI_TEAM_BRIDGE_URL || "http://127.0.0.1:8765").trim();
}

export function resolveTaskTimeoutMs(env = process.env) {
  const value = Number(env.WORKBUDDY_TASK_TIMEOUT_MS);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_TASK_TIMEOUT_MS;
}

export function resolvePermissionMode(env = process.env) {
  return (env.WORKBUDDY_PERMISSION_MODE || DEFAULT_PERMISSION_MODE).trim();
}

export function resolveMaxTurns(env = process.env) {
  const value = Number(env.WORKBUDDY_MAX_TURNS);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : DEFAULT_MAX_TURNS;
}

/**
 * ★ 关键修复：剔除桌面版注入的 SERVER__PORT / SERVER__HOST。
 *
 * 桌面版会让子进程继承 `SERVER__PORT=<桌面版自己的端口>`，CLI 启动时就会照着这个端口
 * 去 listen，撞车后内部 Promise 未捕获拒绝，主流程永久挂起（实测卡死 7 分钟以上）。
 * 详情见 WORKBUDDY_CLI_FINDINGS.md。
 */
export function buildCliEnv(baseEnv = process.env, extra = {}) {
  const env = { ...baseEnv, ...extra };
  delete env.SERVER__PORT;
  delete env.SERVER__HOST;
  // 避免子进程继承 Worker 自身的 node 模块注入，影响 CLI 的 bundle 选择。
  delete env.NODE_OPTIONS;
  return env;
}

/**
 * 选择本次执行的模型。
 * 优先级：task.metadata.model > WORKBUDDY_MODEL > 默认。
 * 校验：必须在 SUPPORTED_MODELS 内（除非 WORKBUDDY_ALLOW_ANY_MODEL=1）。
 * 闸门：付费模型默认禁止（除非 WORKBUDDY_ALLOW_PAID_MODELS=1）。
 */
export function resolveModel({ task, env = process.env } = {}) {
  const requested = String(task?.metadata?.model || env.WORKBUDDY_MODEL || DEFAULT_MODEL).trim();
  const allowAny = env.WORKBUDDY_ALLOW_ANY_MODEL === "1";
  if (!allowAny && !SUPPORTED_MODELS.includes(requested)) {
    const error = new Error(
      `Model "${requested}" is not in the verified WorkBuddy model list. ` +
        `Set WORKBUDDY_ALLOW_ANY_MODEL=1 to override (not recommended).`,
    );
    error.code = ERROR_CODES.MODEL_NOT_ALLOWED;
    throw error;
  }
  const allowPaid = env.WORKBUDDY_ALLOW_PAID_MODELS === "1";
  if (!allowPaid && PAID_MODELS.includes(requested)) {
    const error = new Error(
      `Paid model "${requested}" is blocked by default. Set WORKBUDDY_ALLOW_PAID_MODELS=1 to allow it.`,
    );
    error.code = ERROR_CODES.PAID_MODEL_BLOCKED;
    throw error;
  }
  return requested;
}

// ---------------------------------------------------------------------------
// CLI 参数
// ---------------------------------------------------------------------------

export function buildCliArgs({
  prompt,
  model = DEFAULT_MODEL,
  permissionMode = DEFAULT_PERMISSION_MODE,
  outputFormat = "stream-json",
  maxTurns,
  addDirs = [],
  tools,
  systemPrompt,
} = {}) {
  const args = [
    "--print",
    "--model", model,
    "--permission-mode", permissionMode,
    "--output-format", outputFormat,
  ];
  if (Number.isFinite(maxTurns) && maxTurns > 0) args.push("--max-turns", String(maxTurns));
  for (const dir of addDirs) {
    if (dir) args.push("--add-dir", String(dir));
  }
  // 明确禁用工具：传空串表示「一个工具都不给」，适合纯文字交付型任务。
  if (tools !== undefined && tools !== null) args.push("--tools", String(tools));
  if (systemPrompt) args.push("--system-prompt", String(systemPrompt));
  args.push(prompt);
  return args;
}

// ---------------------------------------------------------------------------
// 错误分类
// ---------------------------------------------------------------------------

const AUTH_PATTERN = /Authentication required|Authentication failed|\/login command|AUTH_REQUIRED|AUTH_UNAUTHORIZED/i;
const RATE_PATTERN = /rate limit|too many requests|\b429\b|quota|overloaded/i;
const UNAVAILABLE_PATTERN = /model_unavailable|not available|service unavailable|\b503\b|\b502\b/i;
const TIMEOUT_PATTERN = /timed out|timeout/i;

/**
 * 把 CLI 的退出码 / 输出 / 异常归类成稳定错误码。
 * 返回 { code, retryable, message }。
 */
export function classifyCliError({ exitCode = 0, stdout = "", stderr = "", error = null } = {}) {
  const haystack = `${stdout}\n${stderr}\n${error?.message || ""}`;
  if (error?.name === "AbortError") {
    return { code: ERROR_CODES.CANCELLED, retryable: false, message: "Task was cancelled" };
  }
  if (error?.code === "ETIMEDOUT" || TIMEOUT_PATTERN.test(haystack)) {
    return { code: ERROR_CODES.TIMEOUT, retryable: true, message: "WorkBuddy CLI timed out" };
  }
  if (error?.code === "ENOENT") {
    return { code: ERROR_CODES.CLI_MISSING, retryable: false, message: "WorkBuddy CLI binary not found" };
  }
  if (AUTH_PATTERN.test(haystack)) {
    return {
      code: ERROR_CODES.AUTH,
      retryable: false,
      message: "WorkBuddy CLI is not authenticated (run an official /login once)",
    };
  }
  if (RATE_PATTERN.test(haystack)) {
    return { code: ERROR_CODES.RATE_LIMIT, retryable: true, message: "WorkBuddy model rate limited" };
  }
  if (UNAVAILABLE_PATTERN.test(haystack)) {
    return { code: ERROR_CODES.MODEL_UNAVAILABLE, retryable: true, message: "WorkBuddy model unavailable" };
  }
  if (exitCode !== 0) {
    return {
      code: ERROR_CODES.CLI_EXIT,
      retryable: true,
      message: `WorkBuddy CLI exited with code ${exitCode}`,
    };
  }
  return { code: null, retryable: false, message: "" };
}

/**
 * 从 stream-json 事件里抽取「最终文本」。
 * CodeBuddy CLI 的输出形态与 Claude Code 类似，但为避免版本漂移导致漏判，
 * 这里做了多形态兜底：result.result / assistant 文本累积 / 顶层 result 字符串。
 */
export function extractFinalText({ events = [], raw = "" } = {}) {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event?.type === "result" && typeof event.result === "string" && event.result.trim()) {
      return event.result.trim();
    }
  }
  const chunks = [];
  for (const event of events) {
    const content = event?.message?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (block?.type === "text" && typeof block.text === "string") chunks.push(block.text);
    }
  }
  if (chunks.join("").trim()) return chunks.join("").trim();
  // 结构化事件已经存在时，stdout 是日志/JSON/提示词回显而非模型正文。
  // 空 result 不能用原始 stdout 冒充任务产物。
  if (events.length > 0) return "";
  return String(raw || "").trim();
}

/**
 * 从 stream-json 事件里抽取用量。
 * 取不到时返回 { known:false }，**绝不假造数字**。
 */
export function extractUsage({ events = [] } = {}) {
  let turns = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  let costUsd = null;
  let sawUsage = false;
  for (const event of events) {
    const isAssistant = event?.type === "assistant";
    if (isAssistant) turns += 1;
    const usage = event?.usage || event?.message?.usage;
    if (usage && typeof usage === "object") {
      sawUsage = true;
      const input = Number(usage.input_tokens ?? usage.inputTokens ?? 0);
      const output = Number(usage.output_tokens ?? usage.outputTokens ?? 0);
      const cacheRead = Number(usage.cache_read_input_tokens ?? 0);
      if (Number.isFinite(input)) inputTokens += input;
      if (Number.isFinite(output)) outputTokens += output;
      if (Number.isFinite(cacheRead)) inputTokens += cacheRead;
    }
    const cost = event?.total_cost_usd ?? event?.cost_usd;
    if (cost !== undefined && cost !== null && Number.isFinite(Number(cost))) {
      costUsd = Number(cost);
    }
  }
  return {
    known: sawUsage || costUsd !== null,
    turns,
    inputTokens: sawUsage ? inputTokens : null,
    outputTokens: sawUsage ? outputTokens : null,
    totalTokens: sawUsage ? inputTokens + outputTokens : null,
    costUsd,
    note: sawUsage || costUsd !== null ? "" : "usage_unknown_by_cli",
  };
}

/** stream-json 事件 → AI Team Progress Event。 */
export function workbuddyProgressFromEvent(event, context = {}) {
  const model = context.model || DEFAULT_MODEL;
  if (event?.type === "system" && (event?.subtype === "init" || event?.subtype === "session_start")) {
    return {
      stage: "model_session",
      message: `WorkBuddy session initialized (${event.model || model})`,
      current: 2,
      total: 5,
    };
  }
  if (event?.type === "assistant") {
    const content = Array.isArray(event?.message?.content) ? event.message.content : [];
    const tool = content.find((item) => item?.type === "tool_use");
    if (tool) {
      return { stage: "tool_use", message: `WorkBuddy is using ${String(tool.name || "a tool")}` };
    }
    const text = content.find((item) => item?.type === "text");
    if (text?.text) {
      return { stage: "generating", message: String(text.text).slice(0, 160) };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// 执行
// ---------------------------------------------------------------------------

/**
 * 起一次 WorkBuddy CLI 调用。
 *
 * - 环境：剔除 SERVER__PORT / SERVER__HOST（卡死修复）；
 * - 超时：墙钟到点先 SIGTERM，宽限 5s 再 SIGKILL；
 * - 取消：AbortSignal 直接触发同一套终止逻辑；
 * - 输出：stream-json 逐行解析，text 模式直接收全文。
 */
export async function runWorkBuddyPrompt({
  task,
  prompt,
  model,
  signal,
  outputFormat = "stream-json",
  onEvent = async () => {},
  cliBin = resolveCliBin(),
  nodeBin = resolveNodeBin(),
  timeoutMs = resolveTaskTimeoutMs(),
  permissionMode = resolvePermissionMode(),
  maxTurns = resolveMaxTurns(),
  addDirs = [],
  tools,
  systemPrompt,
  env = process.env,
} = {}) {
  const args = buildCliArgs({
    prompt,
    model,
    permissionMode,
    outputFormat,
    maxTurns,
    addDirs,
    tools,
    systemPrompt,
  });

  const child = spawn(nodeBin, [cliBin, ...args], {
    cwd: task?.workspace || process.cwd(),
    env: buildCliEnv(env, { NO_COLOR: "1" }),
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  let timedOut = false;
  let killed = false;
  const events = [];

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr = (stderr + chunk).slice(-8_000);
  });

  const terminate = () => {
    if (killed) return;
    killed = true;
    child.kill("SIGTERM");
    const hard = setTimeout(() => child.kill("SIGKILL"), 5_000);
    hard.unref?.();
  };

  const abort = () => terminate();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    terminate();
  }, Math.max(1, Number(timeoutMs) || DEFAULT_TASK_TIMEOUT_MS));

  const output = (async () => {
    child.stdout.setEncoding("utf8");
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    for await (const line of lines) {
      stdout = (stdout + line + "\n").slice(-200_000);
      const trimmed = line.trim();
      if (!trimmed || outputFormat === "text") continue;
      try {
        const event = JSON.parse(trimmed);
        events.push(event);
        await onEvent(event);
      } catch {
        // 非 JSON 行（进度条 / 日志）直接忽略，不视为错误。
      }
    }
  })();

  try {
    const exitCode = await new Promise((resolveClose, rejectError) => {
      child.once("error", rejectError);
      child.once("close", resolveClose);
    });
    await output;

    if (signal?.aborted) {
      const error = new Error("WorkBuddy task cancelled");
      error.name = "AbortError";
      error.code = ERROR_CODES.CANCELLED;
      throw error;
    }
    if (timedOut) {
      const error = new Error(`WorkBuddy timed out after ${timeoutMs}ms`);
      error.code = ERROR_CODES.TIMEOUT;
      throw error;
    }

    const classified = classifyCliError({ exitCode, stdout, stderr });
    if (classified.code) {
      const error = new Error(`${classified.message}: ${(stderr || stdout).trim().slice(-2_000)}`);
      error.code = classified.code;
      error.retryable = classified.retryable;
      throw error;
    }

    if (outputFormat !== "text") {
      const resultEvent = [...events].reverse().find((event) => event?.type === "result");
      if (resultEvent?.is_error === true) {
        const error = new Error("WorkBuddy CLI returned an error result event");
        error.code = ERROR_CODES.CLI_EXIT;
        throw error;
      }
      if (events.length === 0) {
        const error = new Error("WorkBuddy CLI returned no structured output events");
        error.code = ERROR_CODES.EMPTY_OUTPUT;
        throw error;
      }
    }

    const text = extractFinalText({ events, raw: stdout });
    if (!text) {
      const error = new Error("WorkBuddy CLI produced empty output");
      error.code = ERROR_CODES.EMPTY_OUTPUT;
      throw error;
    }

    return {
      text,
      stdout,
      stderr,
      events,
      usage: extractUsage({ events }),
      model: model || DEFAULT_MODEL,
      outputFormat,
    };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

// ---------------------------------------------------------------------------
// 任务契约（Prompt / 产物）
// ---------------------------------------------------------------------------

function messageText(message) {
  if (typeof message === "string") return message;
  if (!message || typeof message !== "object") return "";
  return String(message.content ?? message.text ?? "").trim();
}

export function requiredArtifacts(task) {
  const required = [];
  for (const item of task?.metadata?.expected_artifacts || []) {
    const value = String(item || "").trim();
    if (value && !required.includes(value)) required.push(value);
  }
  return required;
}

export function promptArtifacts(prompt) {
  const pattern = /(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|html?|xml)/g;
  const found = [];
  for (const match of String(prompt || "").matchAll(pattern)) {
    const candidate = match[0].replace(/^\.\//, "");
    if (!found.includes(candidate)) found.push(candidate);
  }
  return found;
}

export function expectedArtifacts(task) {
  const candidates = [...requiredArtifacts(task)];
  for (const candidate of promptArtifacts(task?.prompt)) {
    if (!candidates.includes(candidate)) candidates.push(candidate);
  }
  return candidates;
}

export function promptFor(task, messages = []) {
  const followUps = messages
    .map(messageText)
    .filter(Boolean)
    .map((text, index) => `Follow-up ${index + 1}:\n${text}`)
    .join("\n\n");
  return [
    `You are executing AI Team task ${task.task_id} for the WorkBuddy worker.`,
    `Title: ${task.title}`,
    "",
    task.prompt,
    followUps ? `\nAdditional follow-up messages:\n${followUps}` : "",
    "",
    "Work only inside the requested workspace.",
    "Do not invent successful tool calls or files.",
    "If you must produce a document artifact, return its full body as plain text; the worker adapter persists it.",
    "When a Supervisor follow-up says to finalize/converge, stop starting new work and return the final artifact body.",
  ]
    .filter(Boolean)
    .join("\n");
}

export function isCheckpointableArtifact(relativePath) {
  return /\.(?:md|markdown|txt|log|json|csv|ya?ml)$/i.test(String(relativePath || ""));
}

/** 执行前先落 checkpoint（避免 Supervisor 把「文件存在」误判成「已完成」）。 */
export async function initializeRequiredArtifacts(task) {
  const initialized = [];
  for (const relativePath of requiredArtifacts(task)) {
    if (!isCheckpointableArtifact(relativePath)) continue;
    const target = safeJoin(task.workspace, relativePath);
    try {
      await access(target);
      continue;
    } catch {
      // 不存在才创建
    }
    const parent = target.slice(0, Math.max(0, target.lastIndexOf(sep)));
    if (parent) await mkdir(parent, { recursive: true });
    const skeleton = [
      ARTIFACT_IN_PROGRESS_MARKER,
      "",
      `# ${task.title || "AI Team Task Artifact"}`,
      "",
      `Task: ${task.task_id}`,
      "",
      "> Checkpoint created by the WorkBuddy worker before model execution. Replace before finishing.",
      "",
    ].join("\n");
    await writeFile(target, skeleton, { encoding: "utf8", flag: "wx" }).catch((error) => {
      if (error?.code !== "EEXIST") throw error;
    });
    initialized.push(relativePath);
  }
  return initialized;
}

/** 把模型返回的最终正文可靠落盘（不依赖模型自己调用写文件工具）。 */
export async function persistFinalText(task, text) {
  const body = String(text || "").trim();
  if (!body) return null;
  const candidates = requiredArtifacts(task).filter(isCheckpointableArtifact);
  if (candidates.length !== 1) return null;
  const relativePath = candidates[0];
  const target = safeJoin(task.workspace, relativePath);
  const completedMarker = "AI_TEAM_ARTIFACT_STATUS: completed";
  const content = body.startsWith("AI_TEAM_ARTIFACT_STATUS:") ? body : `${completedMarker}\n\n${body}`;
  const parent = target.slice(0, Math.max(0, target.lastIndexOf(sep)));
  if (parent) await mkdir(parent, { recursive: true });
  await writeFile(target, content.endsWith("\n") ? content : `${content}\n`, "utf8");
  return relativePath;
}

/** 校验并登记产物。 */
export async function collectArtifacts(task) {
  for (const relativePath of requiredArtifacts(task)) {
    const target = safeJoin(task.workspace, relativePath);
    try {
      await access(target);
    } catch {
      const error = new Error(`Expected artifact was not created: ${relativePath}`);
      error.code = ERROR_CODES.ARTIFACT_INCOMPLETE;
      throw error;
    }
    try {
      await assertArtifactReady(task.workspace, relativePath);
    } catch (error) {
      // 统一错误分类：checkpoint 未替换 = 产物不完整（不是「已完成」）。
      error.code = error.code || ERROR_CODES.ARTIFACT_INCOMPLETE;
      throw error;
    }
  }
  const artifacts = [];
  for (const relativePath of expectedArtifacts(task)) {
    try {
      await access(safeJoin(task.workspace, relativePath));
    } catch {
      continue;
    }
    artifacts.push({ path: relativePath, kind: "file", label: relativePath });
  }
  return artifacts;
}

/** 工作区必须存在，且产物路径不得越界（路径穿越防护）。 */
export function safeJoin(workspace, relativePath) {
  const root = resolve(String(workspace || ""));
  if (!root) {
    const error = new Error("Task workspace is required");
    error.code = ERROR_CODES.WORKSPACE_INVALID;
    throw error;
  }
  if (isAbsolute(String(relativePath))) {
    const error = new Error(`Artifact path must be relative: ${relativePath}`);
    error.code = ERROR_CODES.WORKSPACE_ESCAPE;
    throw error;
  }
  const target = resolve(root, String(relativePath));
  if (target !== root && !target.startsWith(root + sep)) {
    const error = new Error(`Artifact path escapes the workspace: ${relativePath}`);
    error.code = ERROR_CODES.WORKSPACE_ESCAPE;
    throw error;
  }
  return target;
}

// ---------------------------------------------------------------------------
// 任务处理器
// ---------------------------------------------------------------------------

async function maybeProgress(ctx, progress) {
  if (typeof ctx.reportProgress === "function") await ctx.reportProgress(progress);
}

export function createTaskHandler({ runPrompt = runWorkBuddyPrompt } = {}) {
  return async function handleTask(ctx) {
    const task = ctx.task;
    const root = resolve(String(task.workspace || ""));
    try {
      await access(root);
    } catch {
      const error = new Error(`Task workspace does not exist: ${task.workspace}`);
      error.code = ERROR_CODES.WORKSPACE_INVALID;
      throw error;
    }

    const model = resolveModel({ task });
    const pending = [];
    let sessionReported = false;
    const unsubscribe =
      typeof ctx.onMessage === "function"
        ? ctx.onMessage((message) => {
            pending.push(message);
          })
        : () => {};

    try {
      await maybeProgress(ctx, {
        stage: "preparing",
        message: `Preparing WorkBuddy CLI session (model=${model})`,
        current: 1,
        total: 5,
      });

      const initialized = await initializeRequiredArtifacts(task);
      await maybeProgress(ctx, {
        stage: "artifact_initialized",
        message: initialized.length
          ? `Initialized ${initialized.length} mandatory artifact checkpoint(s)`
          : "No checkpointable mandatory artifact needed",
        current: 2,
        total: 5,
      });

      const runTurn = async ({ messages = [], persist = true } = {}) => {
        const onEvent = async (event) => {
          const progress = workbuddyProgressFromEvent(event, { model });
          if (!progress) return;
          if (progress.stage === "model_session") {
            if (sessionReported) return;
            sessionReported = true;
          }
          await maybeProgress(ctx, progress);
        };
        const result = await runPrompt({
          task,
          prompt: promptFor(task, messages),
          model,
          signal: ctx.signal,
          onEvent,
          // CodeBuddy CLI 2.164.0 的 --add-dir 是变长选项，会吞掉尾部
          // prompt 参数。spawn 的 cwd 已限定工作区，不需要重复传路径。
          addDirs: [],
        });
        if (persist) await persistFinalText(task, result.text);
        return result;
      };

      await maybeProgress(ctx, {
        stage: "model",
        message: "WorkBuddy CLI is working on the task",
        current: 3,
        total: 5,
      });
      const first = await runTurn({ messages: ctx.getMessages?.() || [] });
      await ctx.refreshMessages?.();

      // 处理执行期间新到的 Supervisor 追加消息（每条各跑一轮，仍受同一墙钟约束）。
      let guard = 0;
      while (pending.length > 0 && guard < 10) {
        guard += 1;
        const followUps = pending.splice(0, pending.length);
        await maybeProgress(ctx, {
          stage: "follow_up",
          message: `WorkBuddy is handling ${followUps.length} follow-up message(s)`,
          current: 4,
          total: 5,
        });
        await runTurn({ messages: followUps });
        await ctx.refreshMessages?.();
      }

      const artifacts = await collectArtifacts(task);
      await maybeProgress(ctx, {
        stage: "artifacts",
        message: `Validated ${artifacts.length} artifact(s)`,
        current: 5,
        total: 5,
      });

      // 用量诚实登记：拿不到就写 unknown，不假造。
      const usage = first.usage || { known: false, note: "usage_unknown_by_cli" };
      if (typeof ctx.reportProgress === "function") {
        await ctx.reportProgress({
          stage: "usage",
          message: usage.known
            ? `turns=${usage.turns} in=${usage.inputTokens ?? "?"} out=${usage.outputTokens ?? "?"} cost=${usage.costUsd ?? "?"}`
            : `usage unknown (${usage.note || "cli did not report"})`,
        });
      }

      return {
        artifacts,
        usage,
        model,
      };
    } finally {
      unsubscribe();
    }
  };
}

export const handleTask = createTaskHandler();

// ---------------------------------------------------------------------------
// Worker 工厂 & 启动
// ---------------------------------------------------------------------------

export function createWorker(options = {}) {
  return new AgentWorker({
    bridgeUrl: resolveBridgeUrl(),
    agentId: "workbuddy",
    label: "WorkBuddy Code",
    capabilities: ["implementation", "research", "code_review", "long_context"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function readSecretFile(path) {
  const file = String(path || "").trim();
  if (!file) return "";
  try {
    return (await readFile(file, "utf8")).trim();
  } catch {
    return "";
  }
}

export async function resolveBootstrapToken(env = process.env) {
  const explicit = String(env.AI_TEAM_BOOTSTRAP_TOKEN || "").trim();
  if (explicit) return explicit;
  return readSecretFile(env.AI_TEAM_BOOTSTRAP_TOKEN_FILE);
}

/** 真实模型预检：起一次最小 CLI 调用，必须原样答对 WORKBUDDY_MODEL_OK。 */
export async function preflightModel({
  timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS,
  runPrompt = runWorkBuddyPrompt,
  model = process.env.WORKBUDDY_MODEL || DEFAULT_MODEL,
} = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    // 预检自身也套一层超时，避免 CLI 卡住把启动流程拖死。
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.max(1, Number(timeoutMs) || DEFAULT_PREFLIGHT_TIMEOUT_MS));
    runPrompt({
      task: { task_id: "workbuddy-model-preflight", title: "WorkBuddy model preflight", workspace: process.cwd(), prompt: READINESS_PROMPT },
      prompt: READINESS_PROMPT,
      model,
      signal: controller.signal,
      outputFormat: "text",
      timeoutMs,
      maxTurns: 1,
      addDirs: [],
    })
      .then((result) => {
        const response = String(result?.text ?? result?.stdout ?? "").trim();
        if (response !== "WORKBUDDY_MODEL_OK") {
          const error = new Error(
            `WorkBuddy readiness failed: expected WORKBUDDY_MODEL_OK, got ${JSON.stringify(response.slice(0, 200))}`,
          );
          error.code = "readiness_token_mismatch";
          rejectPromise(error);
          return;
        }
        resolvePromise({ token: "WORKBUDDY_MODEL_OK" });
      })
      .catch(rejectPromise)
      .finally(() => clearTimeout(timer));
  });
}

export async function startWorkBuddyFromEnvironment({
  client,
  bootstrapToken,
  preflight = () => preflightModel(),
  workerFactory = () => createWorker(),
  maxAttempts = 1,
  ...rest
} = {}) {
  const token = requireBootstrapToken(
    bootstrapToken === undefined ? await resolveBootstrapToken() : bootstrapToken,
  );
  if (client) client.bootstrapToken = token;
  return startWorkBuddyWorker({
    client,
    bootstrapToken: token,
    preflight,
    workerFactory,
    // 最多一次模型预检；即使上层传入无限重试参数也不得放大费用。
    maxAttempts: 1,
    ...rest,
  });
}

// ---------------------------------------------------------------------------
// 入口（node src/index.js 直接启动）
// ---------------------------------------------------------------------------

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({ baseUrl: resolveBridgeUrl() });
  try {
    // 单次模型预检：失败即停止，不因后台重试导致反复消耗。
    const result = await startWorkBuddyFromEnvironment({ client, maxAttempts: 1 });
    if (!result.started) {
      console.error(
        `WorkBuddy worker did not come online (reason=${result.readiness?.reason || "unknown"}). ` +
          `See WORKBUDDY_HANDOFF_RESULT.md for the authentication blocker.`,
      );
      process.exit(1);
    }
  } catch (error) {
    console.error(`WorkBuddy startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
