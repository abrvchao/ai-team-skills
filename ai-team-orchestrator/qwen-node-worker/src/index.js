import { spawn } from "node:child_process";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import { assertArtifactReady } from "../../node-worker/src/artifact_truth.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  READINESS_PROMPT,
  requireBootstrapToken,
  requireProviderCredential,
  runModelPreflight,
  startQwenWorker,
} from "./readiness.js";

const QWEN_TASK_TIMEOUT_MS = Number(process.env.QWEN_TASK_TIMEOUT_MS || 1_800_000);
const DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1";
const DEFAULT_MODEL = "qwen3-coder-plus";

// Qwen Finalization（Qwen 收尾）必须是 delivery-only（只交付）。
// 这里显式排除当前安装版已经注册的核心工具；不要依赖 `*` 通配符，因为真实任务证明该规则不会可靠匹配全部工具。
// max-tool-calls=0 仍作为第二道保险：未来若新增工具未进入此清单，第一次调用也会立即停止，而不会重新进入探索。
export const QWEN_FINALIZATION_EXCLUDED_TOOLS = [
  "exec",
  "run_shell_command",
  "edit",
  "write_file",
  "read_file",
  "zoom_image",
  "grep_search",
  "glob",
  "list_directory",
  "todo_write",
  "web_fetch",
  "web_search",
  "agent",
  "skill",
  "lsp",
  "send_message",
  "save_memory",
  "ask_user_question",
  "cron",
  "cron_list",
  "cron_add",
  "cron_remove",
  "task",
  "task_create",
  "task_get",
  "task_update",
  "task_list",
  "team",
  "team_create",
  "team_get",
  "team_list",
  "team_delete",
  "wait",
  "read_many_files",
  "replace",
  "find_files",
  "search_file_content",
  "google_web_search",
  "image_gen",
];

export function resolveQwenBin() {
  return (process.env.QWEN_BIN || "qwen").trim();
}

export async function readSecretFile(path) {
  const file = String(path || "").trim();
  if (!file) return "";
  try { return (await readFile(file, "utf8")).trim(); } catch { return ""; }
}

export async function resolveQwenApiKey() {
  for (const name of ["QWEN_API_KEY", "DASHSCOPE_API_KEY", "BAILIAN_CODING_PLAN_API_KEY", "BAILIAN_TOKEN_PLAN_API_KEY"]) {
    const value = String(process.env[name] || "").trim();
    if (value) return value;
  }
  return readSecretFile(process.env.QWEN_API_KEY_FILE);
}

export async function resolveBootstrapToken() {
  const explicit = (process.env.AI_TEAM_BOOTSTRAP_TOKEN || "").trim();
  if (explicit) return explicit;
  return readSecretFile(process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE);
}

export function resolveQwenBaseUrl() {
  return (process.env.QWEN_BASE_URL || process.env.OPENAI_BASE_URL || DEFAULT_BASE_URL).trim();
}

export function resolveQwenModel() {
  return (process.env.QWEN_MODEL || process.env.OPENAI_MODEL || DEFAULT_MODEL).trim();
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
    // 0 是合法值：Finalization（收尾）用它明确禁止继续调用工具。
    maxToolCalls: Number.isFinite(maxToolCalls) && maxToolCalls >= 0 ? maxToolCalls : undefined,
  };
}

export function phaseFinalizationInstructions(task) {
  const phase = String(task.metadata?.team_phase || "primary").trim();
  if (phase === "review") {
    return [
      "- Read only the staged primary artifact you were assigned to review; do not inspect unrelated repository files.",
      "- review.md MUST contain exactly these sections: ## ACCEPTED_CHECKS, ## FAILED_CHECKS, ## COUNTER_EVIDENCE, ## UNSUPPORTED_CLAIMS, ## REQUIRED_REPAIR.",
      "- End with VERDICT: accept | repair | insufficient_evidence. If you lack enough evidence, use insufficient_evidence; do not investigate further.",
    ];
  }
  if (phase === "synthesis") {
    return [
      "- You may read only the already-staged inputs/*.md artifacts needed for synthesis.",
      "- Do not run tests or inspect the product repository. Synthesize only the evidence already supplied.",
      "- final.md must explicitly preserve coverage gaps, unresolved disagreements, and evidence limitations.",
    ];
  }
  if (phase === "implementation") {
    return [
      "- Stop making new product edits and stop running tests now.",
      "- Write the mandatory implementation report from actions/evidence already completed.",
      "- The report MUST contain IMPLEMENTATION_STATUS: changed | no_change | blocked and sections ## CHANGED_FILES, ## ACCEPTANCE_EVIDENCE, ## TEST_RESULTS, ## UNRESOLVED.",
      "- If the accepted implementation was not safely completed or verified, use IMPLEMENTATION_STATUS: blocked.",
    ];
  }
  if (phase === "repair") {
    return [
      "- Stop making new edits and stop running tests now.",
      "- Write the repair report only from repair work/evidence already completed.",
      "- The report MUST contain REPAIR_STATUS: changed | no_change | blocked and sections ## FAILED_CHECKS_ADDRESSED, ## CHANGED_FILES, ## ACCEPTANCE_EVIDENCE, ## TEST_RESULTS, ## UNRESOLVED.",
      "- If the failed checks were not safely repaired or verified, use REPAIR_STATUS: blocked.",
    ];
  }
  if (phase === "validation") {
    return [
      "- Stop running new validation commands now; write the validation report from evidence already collected.",
      "- The report MUST contain ## ACCEPTED_CHECKS, ## FAILED_CHECKS, ## DIFF_SCOPE, ## TEST_EVIDENCE, ## REQUIRED_REPAIR.",
      "- End with VERDICT: accept | repair | blocked.",
      // 中文说明：能通过代码、测试或工作区内补证据解决的问题属于 repair；只有外部依赖无法推进才是 blocked。
      "- Use repair when FAILED_CHECKS / REQUIRED_REPAIR can be resolved inside the current workspace, including missing tests or missing executable evidence.",
      "- Use blocked only for an external/resource/permission dependency that prevents any repair from proceeding. If REQUIRED_REPAIR contains actionable items, do not use blocked.",
    ];
  }
  return [
    "- Write a concise primary report with findings, evidence already observed, risks, recommendations, and explicit evidence gaps.",
    "- Do not claim an acceptance check passed unless you already observed supporting evidence before finalization.",
  ];
}

export function finalizePromptFor(task, messages = []) {
  const contract = task.metadata?.task_contract || {};
  const objective = String(contract.objective || task.title || "Finish the delegated task").trim();
  const role = String(contract.role_label || task.metadata?.team_role_label || "delegated worker").trim();
  const artifacts = requiredArtifacts(task);
  const followUps = messages.map(messageText).filter(Boolean).join("\n");
  const finalizationContext = String(task.metadata?.finalization_context || "").trim();
  const suppliedContext = String(task.metadata?.context_excerpt || "").trim();
  // 中文说明：Forced Finalization（强制收尾）是新的模型会话，必须显式继承 Product Contract 上下文；
  // 否则探索阶段明明拿到 Radar/用户证据，收尾阶段却会误报“没有上下文”。
  const authoritativeContext = [finalizationContext, suppliedContext].filter(Boolean).join("\n\n");
  return [
    `You are in FINALIZATION MODE for AI Team task ${task.task_id}.`,
    `Role: ${role}`,
    `Objective: ${objective}`,
    `Mandatory artifact(s): ${artifacts.join(", ") || "the requested task artifact"}`,
    authoritativeContext ? `Evidence already gathered before finalization (authoritative task context):\n${authoritativeContext}` : "",
    followUps ? `Supervisor instruction: ${followUps}` : "",
    "",
    "FINALIZATION MODE RULES (override exploratory behavior):",
    "- Do NOT run tests, searches, git commands, broad shell commands, benchmarks, or new research.",
    "- Do NOT try to complete missing acceptance evidence now.",
    "- Return the complete mandatory artifact body as your plain-text response; the Worker Adapter will persist it. Do not call file tools.",
    "- If evidence is missing, write NOT VERIFIED / EVIDENCE GAP explicitly instead of investigating.",
    "- Do not emit the in_progress checkpoint marker; return a truthful finalized report that records findings and evidence gaps.",
    "- Prefer a short auditable report over a complete-looking but unverified answer.",
    ...phaseFinalizationInstructions(task),
    "- After writing the mandatory artifact, stop immediately.",
  ].filter(Boolean).join("\n");
}

function linkedTurnController(parentSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (parentSignal?.aborted) controller.abort();
  else parentSignal?.addEventListener("abort", abort, { once: true });
  return {
    controller,
    cleanup: () => parentSignal?.removeEventListener("abort", abort),
  };
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
    "Mandatory text artifacts may already contain an AI_TEAM_ARTIFACT_STATUS: in_progress checkpoint. Update that file as you work, and remove/replace the in_progress marker only when the artifact is decision-quality and ready for Supervisor use.",
    "When a Supervisor follow-up says to converge/finalize, stop starting new exploration and finish the mandatory artifact immediately.",
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

export const ARTIFACT_IN_PROGRESS_MARKER = "AI_TEAM_ARTIFACT_STATUS: in_progress";

export function isCheckpointableArtifact(relativePath) {
  return /\.(?:md|markdown|txt|log)$/i.test(String(relativePath || ""));
}

/**
 * Create early mandatory-artifact checkpoints without overwriting real work.
 * The Supervisor treats files containing ARTIFACT_IN_PROGRESS_MARKER as
 * checkpoints, not usable completion artifacts.
 */
export async function initializeRequiredArtifacts(task) {
  const initialized = [];
  for (const relativePath of requiredArtifacts(task)) {
    if (!isCheckpointableArtifact(relativePath)) continue;
    const target = `${task.workspace}/${relativePath}`;
    try {
      await access(target);
      continue;
    } catch {}
    const parent = target.slice(0, Math.max(0, target.lastIndexOf("/")));
    if (parent) await mkdir(parent, { recursive: true });
    const skeleton = [
      ARTIFACT_IN_PROGRESS_MARKER,
      "",
      `# ${task.title || "AI Team Task Artifact"}`,
      "",
      `Task: ${task.task_id}`,
      "",
      "> Checkpoint created by the worker before model exploration. Replace this checkpoint with the decision-quality artifact before finishing.",
      "",
    ].join("\n");
    await writeFile(target, skeleton, { encoding: "utf8", flag: "wx" }).catch((error) => {
      if (error?.code !== "EEXIST") throw error;
    });
    initialized.push(relativePath);
  }
  return initialized;
}

export async function persistFinalizationText(task, text) {
  const body = String(text || "").trim();
  if (!body) return null;
  const artifacts = requiredArtifacts(task).filter(isCheckpointableArtifact);
  if (artifacts.length !== 1) return null;

  const relativePath = artifacts[0];
  const target = `${task.workspace}/${relativePath}`;
  // 收尾阶段不再依赖模型自己调用 write_file；Adapter 负责把最终文本可靠落盘。
  // completed 只表示“报告已写完”，不代表验收通过；报告中的 VERDICT/STATUS 仍由 Supervisor Gate 判断。
  const completedMarker = "AI_TEAM_ARTIFACT_STATUS: completed";
  const content = body.startsWith("AI_TEAM_ARTIFACT_STATUS:")
    ? body
    : `${completedMarker}\n\n${body}`;
  const parent = target.slice(0, Math.max(0, target.lastIndexOf("/")));
  if (parent) await mkdir(parent, { recursive: true });
  await writeFile(target, content.endsWith("\n") ? content : `${content}\n`, "utf8");
  return relativePath;
}

export function expectedArtifacts(task) {
  const candidates = [...requiredArtifacts(task)];
  for (const candidate of promptArtifacts(task.prompt)) {
    if (!candidates.includes(candidate)) candidates.push(candidate);
  }
  return candidates;
}

export function buildQwenArgs({ prompt, outputFormat = "stream-json", maxToolCalls, excludeTools = [] } = {}) {
  const args = [
    "--bare",
    "--approval-mode", process.env.QWEN_APPROVAL_MODE || "auto",
    "--output-format", outputFormat,
    "--auth-type", "openai",
    "--openai-base-url", resolveQwenBaseUrl(),
    "--model", resolveQwenModel(),
    "--max-wall-time", process.env.QWEN_MAX_WALL_TIME || "30m",
  ];
  if (maxToolCalls !== undefined) args.push("--max-tool-calls", String(maxToolCalls));
  // Finalization（收尾）可以通过 deny rule（拒绝规则）从模型上下文中移除工具，而不是等第一次调用后再报错。
  // 数组参数使用 --exclude-tools=<rule>，避免单个规则本身被拆分。
  for (const tool of excludeTools) args.push(`--exclude-tools=${String(tool)}`);
  // 统一使用显式 --prompt，避免 positional prompt（位置提示词）与数组参数解析产生歧义。
  // Qwen 当前仍支持该参数；未来若移除，只需在 Adapter（适配器）这一处迁移。
  args.push("--prompt", prompt);
  return args;
}

export async function runQwenTextFinalization({ task, prompt, signal, timeoutMs = 120_000, fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl is required");
  const apiKey = requireProviderCredential(await resolveQwenApiKey());
  const baseUrl = resolveQwenBaseUrl().replace(/\/$/, "");
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, Math.max(1, Number(timeoutMs) || 120_000));
  try {
    // Finalization（收尾）直接调用兼容 OpenAI 的文本接口，不发送 tools 字段。
    // 这样模型从协议层就没有任何工具可调用，而不是依赖 CLI 的权限规则去“禁止”工具。
    const response = await fetchImpl(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: resolveQwenModel(),
        messages: [{ role: "user", content: String(prompt || "") }],
        temperature: 0,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(`Qwen finalization API HTTP ${response.status}: ${body.slice(-1_000)}`);
    }
    const payload = await response.json();
    const content = payload?.choices?.[0]?.message?.content;
    if (!String(content || "").trim()) {
      throw new Error("Qwen finalization API returned empty text");
    }
    return { stdout: String(content).trim() + "\n", stderr: "", events: [] };
  } catch (error) {
    if (timedOut) {
      const timeout = new Error(`Qwen finalization timed out after ${timeoutMs}ms`);
      timeout.code = "timeout";
      throw timeout;
    }
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export async function runQwenPrompt({ task, prompt, signal, outputFormat = "stream-json", onEvent = async () => {}, qwenBin = resolveQwenBin(), timeoutMs = QWEN_TASK_TIMEOUT_MS, maxToolCalls, excludeTools = [] } = {}) {
  const apiKey = requireProviderCredential(await resolveQwenApiKey());
  const child = spawn(qwenBin, buildQwenArgs({ prompt, outputFormat, maxToolCalls, excludeTools }), {
    cwd: task.workspace,
    env: {
      ...process.env,
      OPENAI_API_KEY: apiKey,
      OPENAI_BASE_URL: resolveQwenBaseUrl(),
      OPENAI_MODEL: resolveQwenModel(),
      NO_COLOR: "1",
    },
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
  const timer = setTimeout(() => { timedOut = true; child.kill("SIGTERM"); }, Math.max(1, Number(timeoutMs) || QWEN_TASK_TIMEOUT_MS));
  const output = (async () => {
    child.stdout.setEncoding("utf8");
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    for await (const line of lines) {
      stdout += line + "\n";
      const trimmed = line.trim();
      if (!trimmed || outputFormat === "text") continue;
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
      const error = new Error("Qwen task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`Qwen timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) throw new Error(`Qwen exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`);
    return { stdout, stderr, events };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function qwenProgressFromEvent(event) {
  if (event?.type === "system" && event?.subtype === "session_start") {
    const model = event?.model || resolveQwenModel();
    return { stage: "model_session", message: `Qwen session initialized (${model})`, current: 2, total: 4 };
  }
  if (event?.type === "assistant") {
    const content = Array.isArray(event?.message?.content) ? event.message.content : [];
    const tool = content.find((item) => item?.type === "tool_use");
    if (tool) return { stage: "tool_use", message: `Qwen is using ${String(tool.name || "tool")}` };
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

export function createTaskHandler({ runPrompt = runQwenPrompt, runFinalization = runQwenTextFinalization } = {}) {
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
      const progress = qwenProgressFromEvent(event);
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
        const runner = finalize ? runFinalization : runPrompt;
        const result = await runner({
          task,
          prompt: finalize ? finalizePromptFor(task, messages) : promptFor(task, messages),
          signal: linked.controller.signal,
          // Exploration（探索）仍走 CLI 的 stream-json；Finalization（收尾）走独立纯文本 API 通道。
          outputFormat: "stream-json",
          onEvent,
          maxToolCalls: budget.maxToolCalls,
          timeoutMs: budget.timeoutMs ?? (
            finalize ? Number(process.env.QWEN_FINALIZE_TIMEOUT_MS || 120_000) : undefined
          ),
        });
        if (finalize) await persistFinalizationText(task, result?.stdout);
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
      const initialized = await initializeRequiredArtifacts(task);
      await maybeProgress(ctx, {
        stage: "artifact_initialized",
        message: initialized.length ? `Initialized ${initialized.length} mandatory artifact checkpoint(s)` : "No checkpointable mandatory artifact needed",
        current: 1,
        total: 5,
      });
      await maybeProgress(ctx, { stage: "preparing", message: "Preparing Qwen Code headless session", current: 2, total: 5 });
      const first = await runTurn({ messages: ctx.getMessages() });
      if (!sessionReported) await maybeProgress(ctx, { stage: "model_session", message: "Qwen session completed model work", current: 2, total: 4 });
      await ctx.refreshMessages();
      if (first.interruptedForFinalize || finalizeRequested) {
        const followUps = pending.splice(0, pending.length);
        await maybeProgress(ctx, { stage: "forced_finalization", message: "Qwen exploration interrupted; entering bounded finalization", current: 3, total: 5 });
        finalizeRequested = false;
        finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: true });
        await ctx.refreshMessages();
      }
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        // 一旦进入收尾阶段，后续消息只能继续收尾，不能重新打开探索预算。
        const needsFinalize = finalizationStarted || followUps.some(isFinalizeMessage);
        if (needsFinalize) {
          finalizeRequested = false;
          finalizationStarted = true;
          await maybeProgress(ctx, { stage: "forced_finalization", message: "Qwen entering bounded finalization", current: 3, total: 5 });
        }
        await runTurn({ messages: followUps, finalize: needsFinalize });
        await ctx.refreshMessages();
      }
      await maybeProgress(ctx, { stage: "finalizing", message: "Qwen produced a final result", current: 4, total: 5 });
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
    agentId: "qwen",
    label: "Qwen Code",
    capabilities: ["implementation", "research", "long_context"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function preflightModel({ timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS, runPrompt = runQwenPrompt } = {}) {
  requireProviderCredential(await resolveQwenApiKey());
  return runModelPreflight({
    timeoutMs,
    runPrompt: async ({ signal }) => {
      const result = await runPrompt({
        task: { task_id: "qwen-model-preflight", title: "Qwen model preflight", workspace: process.cwd(), prompt: READINESS_PROMPT },
        prompt: READINESS_PROMPT,
        signal,
        outputFormat: "text",
        maxToolCalls: 0,
      });
      return { response: String(result.stdout || "").trim() };
    },
  });
}

export async function startQwenFromEnvironment({ client, bootstrapToken, preflight = () => preflightModel(), workerFactory = () => createWorker(), ...rest } = {}) {
  const token = requireBootstrapToken(bootstrapToken === undefined ? await resolveBootstrapToken() : bootstrapToken);
  if (client) client.bootstrapToken = token;
  return startQwenWorker({ client, bootstrapToken: token, preflight, workerFactory, ...rest });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({ baseUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765" });
  try {
    const result = await startQwenFromEnvironment({ client });
    if (!result.started) process.exit(1);
  } catch (error) {
    console.error(`Qwen startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
