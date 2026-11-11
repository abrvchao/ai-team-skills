import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import { assertArtifactReady } from "../../node-worker/src/artifact_truth.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  READINESS_PROMPT,
  requireBootstrapToken,
  runModelPreflight,
  startGrokWorker,
} from "./readiness.js";

const GROK_TASK_TIMEOUT_MS = Number(process.env.GROK_TASK_TIMEOUT_MS || 1_800_000);

export function resolveGrokBin() {
  return (process.env.GROK_BIN || "grok").trim();
}

export async function resolveGrokApiKey() {
  const explicit = String(process.env.XAI_API_KEY || "").trim();
  if (explicit) return explicit;
  const keyFile = String(process.env.XAI_API_KEY_FILE || "").trim();
  if (!keyFile) return "";
  // 中文说明：密钥只在 Worker 本机进程内读取，不写入 argv、日志或任务 artifact。
  try { return (await readFile(keyFile, "utf8")).trim(); } catch { return ""; }
}

export function classifyGrokFailure(text = "") {
  const value = String(text).toLowerCase();
  if (value.includes("usage limit") || value.includes("free grok build usage limit")) return "grok_quota_exhausted";
  if (value.includes("429") || value.includes("rate limit")) return "grok_rate_limited";
  if (
    value.includes("unauthorized") ||
    value.includes("invalid api key") ||
    value.includes("authentication") ||
    value.includes("login required")
  ) return "grok_auth_required";
  return "grok_runtime_error";
}

export async function resolveBootstrapToken() {
  const explicit = (process.env.AI_TEAM_BOOTSTRAP_TOKEN || "").trim();
  if (explicit) return explicit;
  const tokenFile = (process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE || "").trim();
  if (!tokenFile) return "";
  try { return (await readFile(tokenFile, "utf8")).trim(); } catch { return ""; }
}

function messageText(message) {
  if (typeof message === "string") return message;
  if (!message || typeof message !== "object") return "";
  return String(message.content ?? message.text ?? "").trim();
}

export function isFinalizeMessage(message) {
  const text = messageText(message).toLowerCase();
  return /soft deadline|converge now|converge\/finalize|finalize|mandatory artifact is already present|stop all new exploration/.test(text);
}

export function executionBudgetFor(task, mode = "exploration") {
  const raw = task.metadata?.execution_budget?.[mode] || {};
  const timeoutSeconds = Number(raw.timeout_seconds);
  return {
    timeoutMs: Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? timeoutSeconds * 1000 : undefined,
  };
}

export function finalizePromptFor(task, messages = []) {
  const artifacts = requiredArtifacts(task);
  const followUps = messages.map(messageText).filter(Boolean).join("\n");
  const finalizationContext = String(task.metadata?.finalization_context || "").trim();
  const suppliedContext = String(task.metadata?.context_excerpt || "").trim();
  // 中文说明：Forced Finalization（强制收尾）是新的模型会话，必须显式继承 Product Contract 上下文；
  // 否则探索阶段明明拿到 Radar/用户证据，收尾阶段却会误报“没有上下文”。
  const authoritativeContext = [finalizationContext, suppliedContext].filter(Boolean).join("\n\n");
  const phase = String(task.metadata?.team_phase || "primary").trim();
  const repairRules = phase === "repair" ? [
    "- For repair, the report MUST contain REPAIR_STATUS: changed | no_change | blocked.",
    "- Also include ## FAILED_CHECKS_ADDRESSED, ## CHANGED_FILES, ## ACCEPTANCE_EVIDENCE, ## TEST_RESULTS, ## UNRESOLVED.",
  ] : [];
  return [
    `You are in FINALIZATION MODE for AI Team task ${task.task_id}.`,
    `Mandatory artifact(s): ${artifacts.join(", ") || "the requested task artifact"}`,
    authoritativeContext ? `Evidence already gathered before finalization (authoritative task context):\n${authoritativeContext}` : "",
    followUps ? `Supervisor instruction: ${followUps}` : "",
    "",
    "FINALIZATION MODE RULES (override exploratory behavior):",
    "- Do NOT run tests, searches, git commands, broad shell commands, benchmarks, or new research.",
    "- Do NOT try to fill missing evidence now.",
    "- Write the mandatory artifact immediately from evidence already collected.",
    "- If evidence is incomplete, state NOT VERIFIED / EVIDENCE GAP instead of investigating.",
    ...repairRules,
    "- After writing the mandatory artifact, stop immediately.",
  ].filter(Boolean).join("\n");
}

function linkedTurnController(parentSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (parentSignal?.aborted) controller.abort();
  else parentSignal?.addEventListener("abort", abort, { once: true });
  return { controller, cleanup: () => parentSignal?.removeEventListener("abort", abort) };
}

export function promptFor(task, messages = []) {
  const followUps = messages.map(messageText).filter(Boolean)
    .map((text, index) => `Follow-up ${index + 1}:\n${text}`).join("\n\n");
  return [
    `You are executing AI Team task ${task.task_id} in the requested workspace.`,
    `Title: ${task.title}`,
    "",
    task.prompt,
    followUps ? `\nAdditional follow-up messages:\n${followUps}` : "",
    "",
    "Work only inside the requested workspace. Create every requested deliverable.",
    "For research, prefer current primary sources and include source URLs in deliverables.",
    "Do not invent successful tool calls, observations, citations, or files.",
  ].filter(Boolean).join("\n");
}

export function promptArtifacts(prompt) {
  const pattern = /(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|html?|xml|pdf|png|jpe?g|svg)/g;
  const found = [];
  for (const match of String(prompt || "").matchAll(pattern)) {
    const candidate = match[0].replace(/^\.\//, "");
    if (!found.includes(candidate)) found.push(candidate);
  }
  return found;
}

export function requiredArtifacts(task) {
  const required = [];
  if (Array.isArray(task.metadata?.expected_artifacts)) {
    for (const item of task.metadata.expected_artifacts) {
      const value = String(item || "").trim();
      if (value && !required.includes(value)) required.push(value);
    }
  }
  return required;
}

export function expectedArtifacts(task) {
  const candidates = [...requiredArtifacts(task)];
  for (const candidate of promptArtifacts(task.prompt)) {
    if (!candidates.includes(candidate)) candidates.push(candidate);
  }
  return candidates;
}

export function buildGrokArgs({ prompt, workspace, outputFormat = "streaming-json" }) {
  const args = [
    "--no-auto-update", "--no-alt-screen", "--no-plan", "--no-subagents",
    "--cwd", workspace, "-p", prompt, "--output-format", outputFormat,
  ];
  const model = (process.env.GROK_MODEL || "").trim();
  if (model) args.push("--model", model);
  const effort = (process.env.GROK_REASONING_EFFORT || "").trim();
  if (effort) args.push("--reasoning-effort", effort);
  const permissionMode = (process.env.GROK_PERMISSION_MODE || "auto").trim();
  if (permissionMode) args.push("--permission-mode", permissionMode);
  if (process.env.GROK_ALWAYS_APPROVE === "1") args.push("--always-approve");
  return args;
}

export async function runGrokPrompt({ task, prompt, signal, outputFormat = "streaming-json", onEvent = async () => {}, grokBin = resolveGrokBin(), timeoutMs = GROK_TASK_TIMEOUT_MS } = {}) {
  const apiKey = await resolveGrokApiKey();
  const childEnv = { ...process.env };
  // 中文说明：通过环境变量交给 Grok CLI，避免密钥出现在进程命令行和错误日志里。
  if (apiKey) childEnv.XAI_API_KEY = apiKey;
  const child = spawn(grokBin, buildGrokArgs({ prompt, workspace: task.workspace, outputFormat }), {
    cwd: task.workspace,
    env: childEnv,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  let stdout = "";
  let timedOut = false;
  const events = [];
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const abort = () => child.kill("SIGTERM");
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; child.kill("SIGTERM"); }, Math.max(1, Number(timeoutMs) || GROK_TASK_TIMEOUT_MS));
  const output = (async () => {
    child.stdout.setEncoding("utf8");
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    for await (const line of lines) {
      stdout += line + "\n";
      const trimmed = line.trim();
      if (!trimmed || outputFormat === "plain") continue;
      try {
        const event = JSON.parse(trimmed);
        events.push(event);
        await onEvent(event);
      } catch {}
    }
  })();
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    await output;
    if (signal?.aborted) {
      const error = new Error("Grok task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`Grok timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) {
      const detail = (stderr || stdout).trim().slice(-2_000);
      const error = new Error(`Grok exited with code ${code}: ${detail}`);
      error.code = classifyGrokFailure(detail);
      error.exitCode = code;
      throw error;
    }
    return { stdout, stderr, events };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

function updateFromEvent(event) {
  return event?.params?.update ?? event?.update ?? event;
}

export function grokProgressFromEvent(event) {
  const update = updateFromEvent(event);
  const kind = String(update?.sessionUpdate ?? event?.type ?? update?.type ?? "").toLowerCase();
  const serialized = JSON.stringify(update || event || {}).toLowerCase();
  const toolish = kind.includes("tool") || serialized.includes("tool_call") || serialized.includes("toolcall");
  if (toolish) {
    const name = update?.toolName ?? update?.tool_name ?? update?.name ?? update?.title ?? update?.toolCall?.name ?? update?.tool_call?.name ?? "tool";
    return { stage: "tool_use", message: `Grok is using ${String(name)}` };
  }
  if (kind.includes("session") || kind.includes("agent_message") || event?.method === "session/update") {
    return { stage: "model_session", message: "Grok session is active", current: 2, total: 4 };
  }
  return null;
}

async function maybeProgress(ctx, progress) {
  if (typeof ctx.reportProgress === "function") await ctx.reportProgress(progress);
}

export async function collectArtifacts(task) {
  for (const relativePath of requiredArtifacts(task)) {
    try { await access(`${task.workspace}/${relativePath}`); }
    catch { throw new Error(`Expected artifact was not created: ${relativePath}`); }
    await assertArtifactReady(task.workspace, relativePath);
  }
  const artifacts = [];
  for (const relativePath of expectedArtifacts(task)) {
    try { await access(`${task.workspace}/${relativePath}`); } catch { continue; }
    artifacts.push({ path: relativePath, kind: "file", label: relativePath });
  }
  return artifacts;
}

export function createTaskHandler({ runPrompt = runGrokPrompt } = {}) {
  return async function handleTask(ctx) {
    const task = ctx.task;
    const pending = [];
    let sessionReported = false;
    let activeTurnController = null;
    let finalizeRequested = false;
    let finalizationStarted = false;
    const unsubscribe = ctx.onMessage((message) => {
      pending.push(message);
      if (isFinalizeMessage(message)) {
        finalizeRequested = true;
        activeTurnController?.abort();
      }
    });
    const onEvent = async (event) => {
      const progress = grokProgressFromEvent(event);
      if (!progress) return;
      if (progress.stage === "model_session") {
        if (sessionReported) return;
        sessionReported = true;
      }
      await maybeProgress(ctx, progress);
    };
    const runTurn = async ({ messages = [], finalize = false } = {}) => {
      const budget = executionBudgetFor(task, finalize ? "finalization" : "exploration");
      const linked = linkedTurnController(ctx.signal);
      activeTurnController = linked.controller;
      try {
        await runPrompt({
          task,
          prompt: finalize ? finalizePromptFor(task, messages) : promptFor(task, messages),
          signal: linked.controller.signal,
          outputFormat: "streaming-json",
          onEvent,
          // Grok CLI 暂未暴露稳定的工具次数参数，因此只硬隔离时间预算。
          timeoutMs: budget.timeoutMs,
        });
        return { interruptedForFinalize: false };
      } catch (error) {
        if (linked.controller.signal.aborted && finalizeRequested && !ctx.signal?.aborted) {
          return { interruptedForFinalize: true };
        }
        throw error;
      } finally {
        if (activeTurnController === linked.controller) activeTurnController = null;
        linked.cleanup();
      }
    };
    try {
      await maybeProgress(ctx, { stage: "preparing", message: "Preparing Grok headless session", current: 1, total: 5 });
      const first = await runTurn({ messages: ctx.getMessages() });
      if (!sessionReported) {
        sessionReported = true;
        await maybeProgress(ctx, { stage: "model_session", message: "Grok session completed model work", current: 2, total: 5 });
      }
      await ctx.refreshMessages();
      if (first.interruptedForFinalize || finalizeRequested) {
        const followUps = pending.splice(0, pending.length);
        await maybeProgress(ctx, { stage: "forced_finalization", message: "Grok exploration interrupted; entering bounded finalization", current: 3, total: 5 });
        finalizeRequested = false;
        finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: true });
        await ctx.refreshMessages();
      }
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        // 收尾开始后不再允许后续消息重新打开探索模式。
        const needsFinalize = finalizationStarted || followUps.some(isFinalizeMessage);
        if (needsFinalize) {
          finalizationStarted = true;
          finalizeRequested = false;
        }
        await runTurn({ messages: followUps, finalize: needsFinalize });
        await ctx.refreshMessages();
      }
      await maybeProgress(ctx, { stage: "finalizing", message: "Grok produced a final result", current: 4, total: 5 });
      const artifacts = await collectArtifacts(task);
      await maybeProgress(ctx, { stage: "artifacts", message: `Validated ${artifacts.length} artifact(s)`, current: 5, total: 5 });
      return { artifacts };
    } finally { unsubscribe(); }
  };
}

export const handleTask = createTaskHandler();

export function createWorker(options = {}) {
  return new AgentWorker({
    bridgeUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
    agentId: "grok",
    label: "Grok Build CLI",
    capabilities: ["research", "trend_analysis", "adversarial_review"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function preflightModel({ timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS, runPrompt = runGrokPrompt } = {}) {
  return runModelPreflight({
    timeoutMs,
    runPrompt: async ({ signal }) => {
      const result = await runPrompt({
        task: { task_id: "grok-model-preflight", title: "Grok model preflight", workspace: process.cwd(), prompt: READINESS_PROMPT },
        prompt: READINESS_PROMPT,
        signal,
        outputFormat: "plain",
      });
      return { stdout: result.stdout, response: String(result.stdout || "").trim() };
    },
  });
}

export async function startGrokFromEnvironment({ client, bootstrapToken, preflight = () => preflightModel(), workerFactory = () => createWorker(), ...rest } = {}) {
  // 默认永久关闭付费 Grok。只有用户以后明确决定恢复，
  // 并显式设置环境标志时才允许实际调用；保护升级/重启场景。
  if (process.env.AI_TEAM_GROK_PAID_CALLS_OPT_IN !== "1") {
    const error = new Error("Grok paid model disabled: requires explicit AI_TEAM_GROK_PAID_CALLS_OPT_IN=1");
    error.code = "grok_paid_calls_disabled";
    throw error;
  }
  const token = requireBootstrapToken(bootstrapToken === undefined ? await resolveBootstrapToken() : bootstrapToken);
  if (client) client.bootstrapToken = token;
  // 生产启动固定最多一次；调用次数不能被错误重试参数放大。
  return startGrokWorker({ client, bootstrapToken: token, preflight, workerFactory, ...rest, maxAttempts: 1 });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({ baseUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765" });
  try {
    const result = await startGrokFromEnvironment({ client });
    if (!result.started) process.exit(1);
  } catch (error) {
    console.error(`Grok startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
