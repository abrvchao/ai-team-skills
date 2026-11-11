import { spawn } from "node:child_process";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import { assertArtifactReady } from "../../node-worker/src/artifact_truth.js";
import { assertGeminiOAuthReady, oauthOnlyEnvironment } from "./oauth-guard.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  READINESS_PROMPT,
  requireBootstrapToken,
  runModelPreflight,
  startGeminiWorker,
} from "./readiness.js";

const GEMINI_TASK_TIMEOUT_MS = Number(process.env.GEMINI_TASK_TIMEOUT_MS || 1_800_000);

export function resolveGeminiBin() {
  return (process.env.GEMINI_BIN || "gemini").trim();
}

export async function resolveBootstrapToken() {
  const explicit = (process.env.AI_TEAM_BOOTSTRAP_TOKEN || "").trim();
  if (explicit) return explicit;
  const tokenFile = (process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE || "").trim();
  if (!tokenFile) return "";
  try {
    return (await readFile(tokenFile, "utf8")).trim();
  } catch {
    return "";
  }
}

function messageText(message) {
  if (typeof message === "string") return message;
  if (!message || typeof message !== "object") return "";
  return String(message.content ?? message.text ?? "").trim();
}

export function isFinalizeMessage(message) {
  const text = messageText(message).toLowerCase();
  return /soft deadline|converge now|converge\/finalize|finalize|finalization hard boundary|mandatory artifact is already present|validation report is decision-ready|stop all new exploration/.test(text);
}

export function phaseFinalizationInstructions(task) {
  const phase = String(task.metadata?.team_phase || "primary").trim();
  if (phase === "review") return [
    "- Read only the staged primary artifact assigned for review; do not inspect unrelated repository files.",
    "- review.md MUST contain ## ACCEPTED_CHECKS, ## FAILED_CHECKS, ## COUNTER_EVIDENCE, ## UNSUPPORTED_CLAIMS, ## REQUIRED_REPAIR.",
    "- End with VERDICT: accept | repair | insufficient_evidence. If evidence is incomplete, use insufficient_evidence.",
  ];
  if (phase === "synthesis") return [
    "- Read only already-staged inputs/*.md artifacts needed for synthesis.",
    "- Do not run tests or inspect new product files. Synthesize only evidence already supplied.",
    "- final.md must preserve coverage gaps, unresolved disagreements, and evidence limitations.",
  ];
  if (phase === "implementation") return [
    "- Stop making product edits and stop running tests now.",
    "- Write the implementation report only from work/evidence already completed.",
    "- Include IMPLEMENTATION_STATUS: changed | no_change | blocked and ## CHANGED_FILES, ## ACCEPTANCE_EVIDENCE, ## TEST_RESULTS, ## UNRESOLVED.",
  ];
  if (phase === "repair") return [
    "- Stop making new edits and stop running tests now.",
    "- Write the repair report only from repair work/evidence already completed.",
    "- Include REPAIR_STATUS: changed | no_change | blocked and ## FAILED_CHECKS_ADDRESSED, ## CHANGED_FILES, ## ACCEPTANCE_EVIDENCE, ## TEST_RESULTS, ## UNRESOLVED.",
    "- If the failed checks were not safely repaired or verified, use REPAIR_STATUS: blocked.",
  ];
  if (phase === "validation") return [
    "- Stop all validation commands, searches, sub-agents, and new tool calls now.",
    "- Write the validation report only from evidence already collected.",
    "- Include ## ACCEPTED_CHECKS, ## FAILED_CHECKS, ## DIFF_SCOPE, ## TEST_EVIDENCE, ## REQUIRED_REPAIR.",
    "- End with VERDICT: accept | repair | blocked.",
      // 中文说明：能通过代码、测试或工作区内补证据解决的问题属于 repair；只有外部依赖无法推进才是 blocked。
      "- Use repair when FAILED_CHECKS / REQUIRED_REPAIR can be resolved inside the current workspace, including missing tests or missing executable evidence.",
      "- Use blocked only for an external/resource/permission dependency that prevents any repair from proceeding. If REQUIRED_REPAIR contains actionable items, do not use blocked.",
  ];
  return [
    "- Write a concise primary report from evidence already observed, including explicit evidence gaps.",
    "- Do not claim acceptance passed without evidence already collected.",
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
    "- Do NOT invoke sub-agents or run tests, searches, git commands, shell commands, benchmarks, or new research.",
    "- Do NOT try to fill missing evidence now.",
    "- Immediately write/overwrite the mandatory artifact from evidence already known to you or already present in the workspace.",
    "- If evidence is missing, record NOT VERIFIED / EVIDENCE GAP rather than investigating.",
    "- Remove/replace AI_TEAM_ARTIFACT_STATUS: in_progress only when the artifact truthfully records findings and gaps.",
    ...phaseFinalizationInstructions(task),
    "- After writing the mandatory artifact, stop immediately.",
  ].filter(Boolean).join("\n");
}

export function executionBudgetFor(task, mode = "exploration") {
  const raw = task.metadata?.execution_budget?.[mode] || {};
  const timeoutSeconds = Number(raw.timeout_seconds);
  return {
    timeoutMs: Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? timeoutSeconds * 1000 : undefined,
  };
}

function linkedTurnController(parentSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (parentSignal?.aborted) controller.abort();
  else parentSignal?.addEventListener("abort", abort, { once: true });
  return { controller, cleanup: () => parentSignal?.removeEventListener("abort", abort) };
}

export function promptFor(task, messages = []) {
  const followUps = messages
    .map(messageText)
    .filter(Boolean)
    .map((text, index) => `Follow-up ${index + 1}:\n${text}`)
    .join("\n\n");
  return [
    `You are executing AI Team task ${task.task_id} in the requested workspace.`,
    `Title: ${task.title}`,
    "",
    task.prompt,
    followUps ? `\nAdditional follow-up messages:\n${followUps}` : "",
    "",
    "Work only inside the requested workspace. Create every requested deliverable.",
    "Prefer cited, verifiable sources for research. Do not invent successful tool calls or files.",
    "Mandatory text artifacts may already contain an AI_TEAM_ARTIFACT_STATUS: in_progress checkpoint. Replace that marker only when the artifact is decision-ready.",
    "When a Supervisor follow-up says to converge/finalize, stop exploration and finish the mandatory artifact immediately.",
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
  const logicalTaskId = task.metadata?.logical_task_id || task.task_id;
  if (logicalTaskId === "CN-GEMINI-001") {
    const known = "docs/research/china-social/xiaohongshu-data-options.md";
    if (!required.includes(known)) required.push(known);
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
    try { await access(target); continue; } catch {}
    const parent = target.slice(0, Math.max(0, target.lastIndexOf("/")));
    if (parent) await mkdir(parent, { recursive: true });
    const skeleton = [
      ARTIFACT_IN_PROGRESS_MARKER, "", `# ${task.title || "AI Team Task Artifact"}`, "",
      `Task: ${task.task_id}`, "",
      "> Checkpoint created before model exploration. Replace this checkpoint with the decision-quality artifact before finishing.", "",
    ].join("\n");
    await writeFile(target, skeleton, { encoding: "utf8", flag: "wx" }).catch((error) => {
      if (error?.code !== "EEXIST") throw error;
    });
    initialized.push(relativePath);
  }
  return initialized;
}

export function expectedArtifacts(task) {
  const candidates = [...requiredArtifacts(task)];
  for (const candidate of promptArtifacts(task.prompt)) {
    if (!candidates.includes(candidate)) candidates.push(candidate);
  }
  return candidates;
}

function buildGeminiArgs(prompt, outputFormat) {
  const args = [
    "--prompt",
    prompt,
    "--output-format",
    outputFormat,
    "--approval-mode",
    // 默认只能读和评审；需要修改代码的工程任务再显式批准 auto_edit。
    process.env.GEMINI_APPROVAL_MODE || "plan",
  ];
  const model = (process.env.GEMINI_MODEL || "").trim();
  if (model) args.push("--model", model);
  if (process.env.GEMINI_SANDBOX === "1") args.push("--sandbox");
  if (process.env.GEMINI_SKIP_TRUST === "1") args.push("--skip-trust");
  return args;
}

export async function runGeminiPrompt({
  task,
  prompt,
  signal,
  outputFormat = "stream-json",
  onEvent = async () => {},
  geminiBin = resolveGeminiBin(),
  timeoutMs = GEMINI_TASK_TIMEOUT_MS,
  authGate = assertGeminiOAuthReady,
} = {}) {
  // 在每一次真实模型请求前复核订阅登录；严禁使用已有的 Gemini API Key 文件。
  await authGate();
  const ownsProcessGroup = process.platform !== "win32";
  const child = spawn(geminiBin, buildGeminiArgs(prompt, outputFormat), {
    cwd: task.workspace,
    env: oauthOnlyEnvironment(process.env),
    stdio: ["ignore", "pipe", "pipe"],
    // Gemini can spawn nested agent/tool processes. A dedicated process group
    // lets Supervisor finalization terminate the whole exploration tree rather
    // than orphaning descendants that keep stdout open forever.
    detached: ownsProcessGroup,
  });

  let stderr = "";
  let stdout = "";
  let timedOut = false;
  const events = [];

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  let forceKillTimer;
  const signalProcessTree = (signalName) => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    if (ownsProcessGroup && child.pid) {
      try {
        process.kill(-child.pid, signalName);
        return;
      } catch (error) {
        if (error?.code !== "ESRCH") {
          // Fall back to the direct child; the force-kill pass will retry the group.
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
      }, Math.max(100, Number(process.env.GEMINI_ABORT_GRACE_MS || 1_500)));
      forceKillTimer.unref?.();
    }
  };
  const abort = () => terminateChild();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    terminateChild();
  }, Math.max(1, Number(timeoutMs) || GEMINI_TASK_TIMEOUT_MS));

  const output = (async () => {
    child.stdout.setEncoding("utf8");
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    for await (const line of lines) {
      stdout += line + "\n";
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const event = JSON.parse(trimmed);
        events.push(event);
        await onEvent(event);
      } catch {
        // Preserve non-JSON output for diagnostics; process exit still decides success.
      }
    }
  })();

  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    await output;

    if (signal?.aborted) {
      const error = new Error("Gemini task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`Gemini timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) {
      throw new Error(
        `Gemini exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`,
      );
    }
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

async function maybeProgress(ctx, progress) {
  if (typeof ctx.reportProgress === "function") {
    await ctx.reportProgress(progress);
  }
}

export async function collectArtifacts(task) {
  for (const relativePath of requiredArtifacts(task)) {
    try {
      await access(`${task.workspace}/${relativePath}`);
    } catch {
      throw new Error(`Expected artifact was not created: ${relativePath}`);
    }
    await assertArtifactReady(task.workspace, relativePath);
  }
  const artifacts = [];
  for (const relativePath of expectedArtifacts(task)) {
    try {
      await access(`${task.workspace}/${relativePath}`);
    } catch {
      continue;
    }
    artifacts.push({ path: relativePath, kind: "file", label: relativePath });
  }
  return artifacts;
}

export function createTaskHandler({ runPrompt = runGeminiPrompt } = {}) {
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

    const onEvent = async (event) => {
      if (event?.type === "init") {
        await maybeProgress(ctx, {
          stage: "model_session",
          message: `Gemini session initialized${event.model ? ` (${event.model})` : ""}`,
          current: 2,
          total: 5,
        });
      } else if (event?.type === "tool_use") {
        const name = event.tool_name || event.name || "tool";
        await maybeProgress(ctx, { stage: "tool_use", message: `Gemini is using ${name}` });
      } else if (event?.type === "result") {
        await maybeProgress(ctx, { stage: "finalizing", message: "Gemini produced a final result", current: 4, total: 5 });
      }
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
          outputFormat: "stream-json",
          onEvent,
          // Gemini CLI 当前由独立子进程实现预算隔离；工具次数能力暂不伪造。
          timeoutMs: budget.timeoutMs ?? (
            finalize ? Number(process.env.GEMINI_FINALIZE_TIMEOUT_MS || 60_000) : undefined
          ),
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
      const initialized = await initializeRequiredArtifacts(task);
      await maybeProgress(ctx, {
        stage: "artifact_initialized",
        message: initialized.length ? `Initialized ${initialized.length} mandatory artifact checkpoint(s)` : "No checkpointable mandatory artifact needed",
        current: 1,
        total: 5,
      });
      await maybeProgress(ctx, { stage: "preparing", message: "Preparing Gemini headless session", current: 2, total: 5 });

      const first = await runTurn({ messages: ctx.getMessages() });
      await ctx.refreshMessages();
      if (first.interruptedForFinalize || finalizeRequested) {
        const followUps = pending.splice(0, pending.length);
        await maybeProgress(ctx, { stage: "forced_finalization", message: "Gemini exploration interrupted; entering bounded finalization", current: 3, total: 5 });
        finalizeRequested = false;
        finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: true });
        await ctx.refreshMessages();
      }
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        // 收尾阶段只能继续整理已有证据，不能因后续消息重启探索。
        const needsFinalize = finalizationStarted || followUps.some(isFinalizeMessage);
        if (needsFinalize) {
          finalizeRequested = false;
          finalizationStarted = true;
          await maybeProgress(ctx, { stage: "forced_finalization", message: "Gemini entering bounded finalization", current: 3, total: 5 });
        }
        await runTurn({ messages: followUps, finalize: needsFinalize });
        await ctx.refreshMessages();
      }

      const artifacts = await collectArtifacts(task);
      await maybeProgress(ctx, { stage: "artifacts", message: `Validated ${artifacts.length} artifact(s)`, current: 5, total: 5 });
      return { artifacts };
    } finally {
      unsubscribe();
    }
  };
}

export const handleTask = createTaskHandler();

export function createWorker(options = {}) {
  return new AgentWorker({
    bridgeUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
    agentId: "gemini",
    label: "Gemini CLI",
    capabilities: ["research", "long_context", "multimodal"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function preflightModel({
  timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS,
  runPrompt = runGeminiPrompt,
} = {}) {
  return runModelPreflight({
    timeoutMs,
    runPrompt: async ({ prompt, signal }) => {
      const result = await runPrompt({
        task: {
          task_id: "gemini-model-preflight",
          title: "Gemini model preflight",
          workspace: process.cwd(),
          prompt,
        },
        prompt: READINESS_PROMPT,
        signal,
        outputFormat: "json",
      });
      return {
        stdout: result.stdout,
        response: responseFromJsonEvents(result.events, result.stdout),
      };
    },
  });
}

export async function startGeminiFromEnvironment({
  client,
  bootstrapToken,
  preflight = () => preflightModel(),
  workerFactory = () => createWorker(),
  ...rest
} = {}) {
  await assertGeminiOAuthReady();
  const token = requireBootstrapToken(
    bootstrapToken === undefined ? await resolveBootstrapToken() : bootstrapToken,
  );
  if (client) client.bootstrapToken = token;
  return startGeminiWorker({
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
    const result = await startGeminiFromEnvironment({ client });
    if (!result.started) process.exit(1);
  } catch (error) {
    console.error(`Gemini startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
