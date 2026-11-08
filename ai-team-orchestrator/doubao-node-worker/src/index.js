import { spawn } from "node:child_process";
import { access, readFile, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import { assertArtifactReady } from "../../node-worker/src/artifact_truth.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  QUOTA_EXCEEDED,
  RATE_LIMITED,
  READINESS_PROMPT,
  READINESS_TOKEN,
  requireBootstrapToken,
  requireProviderCredential,
  startDoubaoWorker,
} from "./readiness.js";

const DOUBAO_TASK_TIMEOUT_MS = Number(process.env.DOUBAO_TASK_TIMEOUT_MS || 1_800_000);
const DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3";
const DEFAULT_MODEL = "doubao-seed-2-1-pro-260628";
const DEFAULT_MAX_STEPS = "60";
const TRAE_CONFIG_FILE = fileURLToPath(new URL("../trae_config.yaml", import.meta.url));
const ANSI_RE = /\x1b\[[0-9;]*m/g;

export function resolveTraeBin() {
  return (process.env.TRAE_BIN || "trae-cli").trim();
}

export async function readSecretFile(path) {
  const file = String(path || "").trim();
  if (!file) return "";
  try { return (await readFile(file, "utf8")).trim(); } catch { return ""; }
}

export async function resolveDoubaoApiKey() {
  for (const name of ["DOUBAO_API_KEY", "ARK_API_KEY"]) {
    const value = String(process.env[name] || "").trim();
    if (value) return value;
  }
  return readSecretFile(process.env.DOUBAO_API_KEY_FILE);
}

export async function resolveBootstrapToken() {
  const explicit = (process.env.AI_TEAM_BOOTSTRAP_TOKEN || "").trim();
  if (explicit) return explicit;
  return readSecretFile(process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE);
}

export function resolveDoubaoBaseUrl() {
  return (process.env.DOUBAO_BASE_URL || process.env.ARK_BASE_URL || DEFAULT_BASE_URL).replace(/\/$/, "");
}

export function resolveDoubaoModel() {
  return (process.env.DOUBAO_MODEL || DEFAULT_MODEL).trim();
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
  const maxToolCalls = Number(raw.max_tool_calls);
  return {
    timeoutMs: Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? timeoutSeconds * 1000 : undefined,
    maxSteps: Number.isFinite(maxToolCalls) && maxToolCalls > 0 ? maxToolCalls : undefined,
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
    "Do not invent successful tool calls or files.",
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
  for (const item of task.metadata?.expected_artifacts || []) {
    const value = String(item || "").trim();
    if (value && !required.includes(value)) required.push(value);
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

export async function preflightModel({ fetchImpl = fetch, timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS } = {}) {
  const apiKey = requireProviderCredential(await resolveDoubaoApiKey());
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${resolveDoubaoBaseUrl()}/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model: resolveDoubaoModel(),
        messages: [{ role: "user", content: READINESS_PROMPT }],
        temperature: 0,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      let payload = {};
      try { payload = JSON.parse(text); } catch { /* 非 JSON 错误只保留 HTTP 状态。 */ }
      const providerCode = String(payload?.error?.code || payload?.code || "");
      const reason = providerCode === "SetLimitExceeded"
        ? QUOTA_EXCEEDED
        : response.status === 429 ? RATE_LIMITED : "provider_http_error";
      // 服务端原始错误可能包含账号或凭据，不得写入日志。
      const error = new Error(`Doubao readiness failed: HTTP ${response.status}; category=${reason}`);
      error.code = reason;
      error.httpStatus = response.status;
      throw error;
    }
    const payload = await response.json();
    const content = String(payload?.choices?.[0]?.message?.content || "").trim();
    if (content !== READINESS_TOKEN) {
      const error = new Error(`Doubao readiness failed: expected ${READINESS_TOKEN}, got ${JSON.stringify(content.slice(0, 200))}`);
      error.code = "readiness_token_mismatch";
      throw error;
    }
    return { token: READINESS_TOKEN };
  } finally {
    clearTimeout(timer);
  }
}

export function buildTraeArgs({ promptFile, workspace, maxSteps } = {}) {
  return [
    "run",
    "--file", promptFile,
    "--provider", "doubao",
    "--model", resolveDoubaoModel(),
    "--model-base-url", resolveDoubaoBaseUrl(),
    "--max-steps", String(maxSteps ?? process.env.DOUBAO_MAX_STEPS ?? DEFAULT_MAX_STEPS),
    "--working-dir", workspace,
    "--config-file", process.env.DOUBAO_TRAE_CONFIG || TRAE_CONFIG_FILE,
    "--console-type", "simple",
  ];
}

function retainTail(text, chunk, limit = 100_000) {
  const value = text + chunk;
  return value.length > limit ? value.slice(-limit) : value;
}

function toolNameFromLine(line) {
  const clean = String(line || "").replace(ANSI_RE, "");
  for (const name of ["str_replace_based_edit_tool", "sequentialthinking", "task_done", "bash"]) {
    if (clean.includes(name)) return name;
  }
  return "";
}

export async function runDoubaoTask({ task, prompt, signal, onTool = async () => {}, traeBin = resolveTraeBin(), timeoutMs = DOUBAO_TASK_TIMEOUT_MS, maxSteps } = {}) {
  const apiKey = requireProviderCredential(await resolveDoubaoApiKey());
  const safeId = String(task.task_id || "task").replace(/[^A-Za-z0-9_.-]/g, "_");
  const promptFile = join(tmpdir(), `ai-team-doubao-${safeId}.txt`);
  await writeFile(promptFile, prompt, "utf8");
  const child = spawn(traeBin, buildTraeArgs({ promptFile, workspace: task.workspace, maxSteps }), {
    cwd: task.workspace,
    env: {
      ...process.env,
      DOUBAO_API_KEY: apiKey,
      DOUBAO_BASE_URL: resolveDoubaoBaseUrl(),
      NO_COLOR: "1",
      TERM: "dumb",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  let timedOut = false;
  const seenTools = new Set();
  const abort = () => child.kill("SIGTERM");
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; child.kill("SIGTERM"); }, Math.max(1, Number(timeoutMs) || DOUBAO_TASK_TIMEOUT_MS));
  const consume = async (stream, target) => {
    stream.setEncoding("utf8");
    const lines = createInterface({ input: stream, crlfDelay: Infinity });
    for await (const line of lines) {
      if (target === "stdout") stdout = retainTail(stdout, line + "\n");
      else stderr = retainTail(stderr, line + "\n");
      const tool = toolNameFromLine(line);
      if (tool && !seenTools.has(tool)) {
        seenTools.add(tool);
        await onTool(tool);
      }
    }
  };
  const readers = Promise.all([consume(child.stdout, "stdout"), consume(child.stderr, "stderr")]);
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    await readers;
    if (signal?.aborted) {
      const error = new Error("Doubao task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`Doubao timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) throw new Error(`Trae/Doubao exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`);
    return { stdout, stderr };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    await unlink(promptFile).catch(() => {});
  }
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

export function createTaskHandler({ runTask = runDoubaoTask } = {}) {
  return async function handleTask(ctx) {
    const task = ctx.task;
    const pending = [];
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
    const onTool = async (tool) => maybeProgress(ctx, { stage: "tool_use", message: `Doubao/Trae is using ${tool}` });
    const runTurn = async ({ messages = [], finalize = false } = {}) => {
      const budget = executionBudgetFor(task, finalize ? "finalization" : "exploration");
      const linked = linkedTurnController(ctx.signal);
      activeTurnController = linked.controller;
      try {
        await runTask({
          task,
          prompt: finalize ? finalizePromptFor(task, messages) : promptFor(task, messages),
          signal: linked.controller.signal,
          onTool,
          // Trae 的 max_steps 可以作为工具/步骤预算的硬限制。
          maxSteps: budget.maxSteps,
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
      await maybeProgress(ctx, { stage: "preparing", message: "Preparing Doubao via Trae Agent", current: 1, total: 5 });
      await maybeProgress(ctx, { stage: "model_session", message: `Doubao session initialized (${resolveDoubaoModel()})`, current: 2, total: 5 });
      const first = await runTurn({ messages: ctx.getMessages() });
      await ctx.refreshMessages();
      if (first.interruptedForFinalize || finalizeRequested) {
        const followUps = pending.splice(0, pending.length);
        await maybeProgress(ctx, { stage: "forced_finalization", message: "Doubao exploration interrupted; entering bounded finalization", current: 3, total: 5 });
        finalizeRequested = false;
        finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: true });
        await ctx.refreshMessages();
      }
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        // 收尾开始后，任何后续消息都只能继续收尾，不能重新消耗探索预算。
        const needsFinalize = finalizationStarted || followUps.some(isFinalizeMessage);
        if (needsFinalize) {
          finalizationStarted = true;
          finalizeRequested = false;
        }
        await runTurn({ messages: followUps, finalize: needsFinalize });
        await ctx.refreshMessages();
      }
      await maybeProgress(ctx, { stage: "finalizing", message: "Doubao produced a final result", current: 4, total: 5 });
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
    agentId: "doubao",
    label: "Doubao via Trae Agent",
    capabilities: ["research", "chinese_content", "multimodal"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function startDoubaoFromEnvironment({ client, bootstrapToken, preflight = () => preflightModel(), workerFactory = () => createWorker(), ...rest } = {}) {
  const token = requireBootstrapToken(bootstrapToken === undefined ? await resolveBootstrapToken() : bootstrapToken);
  if (client) client.bootstrapToken = token;
  return startDoubaoWorker({ client, bootstrapToken: token, preflight, workerFactory, ...rest });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({ baseUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765" });
  try {
    const result = await startDoubaoFromEnvironment({ client });
    if (!result.started) process.exit(1);
  } catch (error) {
    console.error(`Doubao startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
