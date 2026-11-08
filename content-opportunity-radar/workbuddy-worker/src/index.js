import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { homedir } from "node:os";
import { join } from "node:path";
import { AgentWorker, BridgeClient } from "../../../ai-team-orchestrator/node-worker/src/index.js";
import { assertArtifactReady } from "../../../ai-team-orchestrator/node-worker/src/artifact_truth.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  READINESS_PROMPT,
  requireBootstrapToken,
  runModelPreflight,
  startWorkbuddyWorker,
} from "./readiness.js";

const CODEBUDDY_TASK_TIMEOUT_MS = Number(process.env.CODEBUDDY_TASK_TIMEOUT_MS || 1_800_000);
const DEFAULT_MODEL = "auto";

const DEFAULT_CODEBUDDY_PATHS = [
  "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy",
];

export function resolveCodebuddyBin() {
  const explicit = (process.env.CODEBUDDY_BIN || "").trim();
  if (explicit) return explicit;
  for (const candidate of DEFAULT_CODEBUDDY_PATHS) {
    if (existsSync(candidate)) return candidate;
  }
  return "codebuddy";
}

export function resolveCodebuddyModel() {
  return (process.env.CODEBUDDY_MODEL || DEFAULT_MODEL).trim();
}

export function resolveCodebuddyEffort() {
  return (process.env.CODEBUDDY_EFFORT || "").trim();
}

export function resolveMaxTurns() {
  const value = Number(process.env.CODEBUDDY_MAX_TURNS || 100);
  return Number.isFinite(value) && value > 0 ? value : 100;
}

export async function resolveBootstrapToken() {
  const explicit = (process.env.AI_TEAM_BOOTSTRAP_TOKEN || "").trim();
  if (explicit) return explicit;
  return readSecretFile(process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE);
}

export async function readSecretFile(path) {
  const file = String(path || "").trim();
  if (!file) return "";
  try { return (await readFile(file, "utf8")).trim(); } catch { return ""; }
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
    maxToolCalls: Number.isFinite(maxToolCalls) && maxToolCalls >= 0 ? maxToolCalls : undefined,
  };
}

export function phaseFinalizationInstructions(task) {
  const phase = String(task.metadata?.team_phase || "primary").trim();
  if (phase === "review") {
    return [
      "- Read only the staged primary artifact you were assigned to review; do not inspect unrelated repository files.",
      "- review.md MUST contain exactly these sections: ## ACCEPTED_CHECKS, ## FAILED_CHECKS, ## COUNTER_EVIDENCE, ## UNSUPPORTED_CLAIMS, ## REQUIRED_REPAIR.",
      "- End with VERDICT: accept | repair | insufficient_evidence.",
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
    ];
  }
  if (phase === "repair") {
    return [
      "- Stop making new edits and stop running tests now.",
      "- Write the repair report only from repair work/evidence already completed.",
      "- The report MUST contain REPAIR_STATUS: changed | no_change | blocked and sections ## FAILED_CHECKS_ADDRESSED, ## CHANGED_FILES, ## ACCEPTANCE_EVIDENCE, ## TEST_RESULTS, ## UNRESOLVED.",
    ];
  }
  if (phase === "validation") {
    return [
      "- Stop running new validation commands now; write the validation report from evidence already collected.",
      "- The report MUST contain ## ACCEPTED_CHECKS, ## FAILED_CHECKS, ## DIFF_SCOPE, ## TEST_EVIDENCE, ## REQUIRED_REPAIR.",
      "- End with VERDICT: accept | repair | blocked.",
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

export function buildCodebuddyArgs({ prompt, workspace, outputFormat = "stream-json", maxTurns, disableTools = false } = {}) {
  const args = [
    "-p",
    "--output-format", outputFormat,
    "--permission-mode", process.env.CODEBUDDY_APPROVAL_MODE || "auto",
    "--no-session-persistence",
  ];

  const turns = maxTurns ?? resolveMaxTurns();
  args.push("--max-turns", String(turns));

  if (disableTools) {
    args.push("--tools", "");
  }

  const model = resolveCodebuddyModel();
  if (model && model !== "auto") {
    args.push("--model", model);
  }

  const effort = resolveCodebuddyEffort();
  if (effort) {
    args.push("--effort", effort);
  }

  if (process.env.WORKBUDDY_USE_WORKTREE === "1") {
    const branchName = `ai-team-${Date.now()}`;
    args.push("--worktree", branchName);
    const baseBranch = process.env.WORKBUDDY_WORKTREE_BRANCH || "main";
    args.push("--worktree-branch", baseBranch);
  }

  args.push(prompt);
  return args;
}

export async function runCodebuddyPrompt({
  task,
  prompt,
  signal,
  outputFormat = "stream-json",
  onEvent = async () => {},
  codebuddyBin = resolveCodebuddyBin(),
  timeoutMs = CODEBUDDY_TASK_TIMEOUT_MS,
  maxTurns,
  disableTools = false,
} = {}) {
  const ownsProcessGroup = process.platform !== "win32";
  const child = spawn(codebuddyBin, buildCodebuddyArgs({
    prompt,
    workspace: task.workspace,
    outputFormat,
    maxTurns,
    disableTools,
  }), {
    cwd: task.workspace,
    env: {
      ...process.env,
      NO_COLOR: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
    detached: ownsProcessGroup,
  });

  let stderr = "";
  let stdout = "";
  let timedOut = false;
  const events = [];

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  let forceKillTimer;
  const signalProcessTree = (signalName) => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    if (ownsProcessGroup && child.pid) {
      try {
        process.kill(-child.pid, signalName);
        return;
      } catch (error) {
        if (error?.code !== "ESRCH") {
          child.kill(signalName);
          return;
        }
      }
    }
    child.kill(signalName);
  };
  const terminateChild = () => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    signalProcessTree("SIGTERM");
    if (!forceKillTimer) {
      forceKillTimer = setTimeout(() => {
        if (child.exitCode === null && child.signalCode === null) signalProcessTree("SIGKILL");
      }, Math.max(100, Number(process.env.CODEBUDDY_ABORT_GRACE_MS || 1_500)));
      forceKillTimer.unref?.();
    }
  };
  const abort = () => terminateChild();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    terminateChild();
  }, Math.max(1, Number(timeoutMs) || CODEBUDDY_TASK_TIMEOUT_MS));

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
      const error = new Error("CodeBuddy task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`CodeBuddy timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) throw new Error(`CodeBuddy exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`);
    return { stdout, stderr, events };
  } finally {
    clearTimeout(timer);
    if (forceKillTimer) clearTimeout(forceKillTimer);
    signal?.removeEventListener("abort", abort);
  }
}

export function responseFromJsonEvents(events, stdout = "") {
  for (const event of events) {
    if (typeof event?.response === "string") return event.response;
    if (event?.type === "result" && typeof event?.response === "string") {
      return event.response;
    }
  }
  try {
    const parsed = JSON.parse(String(stdout || "").trim());
    if (typeof parsed?.response === "string") return parsed.response;
  } catch {}
  return "";
}

export function codebuddyProgressFromEvent(event) {
  const kind = String(event?.type || event?.subtype || "").toLowerCase();
  const serialized = JSON.stringify(event || {}).toLowerCase();

  if (kind.includes("session") || kind.includes("init") || kind.includes("start")) {
    const model = event?.model || resolveCodebuddyModel();
    return { stage: "model_session", message: `CodeBuddy session initialized${model !== "auto" ? ` (${model})` : ""}`, current: 2, total: 5 };
  }
  const toolish = kind.includes("tool") || serialized.includes("tool_call") || serialized.includes("toolcall");
  if (toolish) {
    const name = event?.tool_name || event?.name || event?.toolCall?.name || event?.tool_call?.name || "tool";
    return { stage: "tool_use", message: `CodeBuddy is using ${String(name)}` };
  }
  if (kind.includes("result") || kind.includes("complete") || kind.includes("done") || kind.includes("finish")) {
    return { stage: "finalizing", message: "CodeBuddy produced a final result", current: 4, total: 5 };
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

export function createTaskHandler({ runPrompt = runCodebuddyPrompt } = {}) {
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
      const progress = codebuddyProgressFromEvent(event);
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
        const result = await runPrompt({
          task,
          prompt: finalize ? finalizePromptFor(task, messages) : promptFor(task, messages),
          signal: linked.controller.signal,
          outputFormat: "stream-json",
          onEvent,
          maxTurns: finalize ? 1 : budget.maxToolCalls ?? undefined,
          disableTools: finalize,
          timeoutMs: budget.timeoutMs ?? (
            finalize ? Number(process.env.CODEBUDDY_FINALIZE_TIMEOUT_MS || 120_000) : undefined
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
      await maybeProgress(ctx, { stage: "preparing", message: "Preparing CodeBuddy headless session", current: 2, total: 5 });
      const first = await runTurn({ messages: ctx.getMessages() });
      if (!sessionReported) await maybeProgress(ctx, { stage: "model_session", message: "CodeBuddy session completed model work", current: 2, total: 5 });
      await ctx.refreshMessages();
      if (first.interruptedForFinalize || finalizeRequested) {
        const followUps = pending.splice(0, pending.length);
        await maybeProgress(ctx, { stage: "forced_finalization", message: "CodeBuddy exploration interrupted; entering bounded finalization", current: 3, total: 5 });
        finalizeRequested = false;
        finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: true });
        await ctx.refreshMessages();
      }
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        const needsFinalize = finalizationStarted || followUps.some(isFinalizeMessage);
        if (needsFinalize) {
          finalizeRequested = false;
          finalizationStarted = true;
          await maybeProgress(ctx, { stage: "forced_finalization", message: "CodeBuddy entering bounded finalization", current: 3, total: 5 });
        }
        await runTurn({ messages: followUps, finalize: needsFinalize });
        await ctx.refreshMessages();
      }
      await maybeProgress(ctx, { stage: "finalizing", message: "CodeBuddy produced a final result", current: 4, total: 5 });
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
    agentId: "workbuddy",
    label: "WorkBuddy Code",
    capabilities: ["implementation", "research", "code_review", "testing"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function preflightModel({ timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS, runPrompt = runCodebuddyPrompt } = {}) {
  return runModelPreflight({
    timeoutMs,
    runPrompt: async ({ signal }) => {
      const result = await runPrompt({
        task: { task_id: "workbuddy-model-preflight", title: "WorkBuddy model preflight", workspace: process.cwd(), prompt: READINESS_PROMPT },
        prompt: READINESS_PROMPT,
        signal,
        outputFormat: "text",
        maxTurns: 1,
      });
      return { response: String(result.stdout || "").trim() };
    },
  });
}

export async function startWorkbuddyFromEnvironment({
  client,
  bootstrapToken,
  preflight = () => preflightModel(),
  workerFactory = () => createWorker(),
  ...rest
} = {}) {
  const token = requireBootstrapToken(
    bootstrapToken === undefined ? await resolveBootstrapToken() : bootstrapToken,
  );
  if (client) client.bootstrapToken = token;
  return startWorkbuddyWorker({
    client,
    bootstrapToken: token,
    preflight,
    workerFactory,
    ...rest,
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({
    baseUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
  });
  try {
    const result = await startWorkbuddyFromEnvironment({ client });
    if (!result.started) process.exit(1);
  } catch (error) {
    console.error(`WorkBuddy startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
