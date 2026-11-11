import { spawn } from "node:child_process";
import { assertInternationalFreeEligible, MODEL as DSH_INTL_MODEL } from "./international-free-guard.js";
import {
  ensureInternationalElectronHelper, refreshBeforePreflightIfApproved,
} from "./catalog-refresh.js";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import { assertArtifactReady } from "../../node-worker/src/artifact_truth.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  requireBootstrapToken,
  runModelPreflight,
  startDshWorker,
} from "./readiness.js";

const NODE_BIN = process.env.DSH_NODE_BIN || process.execPath;
const DSH_TASK_TIMEOUT_MS = Number(process.env.DSH_TASK_TIMEOUT_MS || 1_800_000);
const DSH_PROFILE = process.env.DSH_PROFILE || "headless";

/** DSH home derives from $HOME so the repository never embeds a machine path. */
export function resolveDshHome() {
  const explicit = (process.env.DSH_HOME || "").trim();
  return explicit || join(homedir(), ".dsh");
}

/**
 * Resolve the installed DSH entrypoint.
 *
 * `DSH_BIN` wins; otherwise the installed `@deepseek-ai/dsh` package under the
 * DSH home is used. Discovery is deterministic and refuses to guess.
 */
export function resolveDshBin() {
  const explicit = (process.env.DSH_BIN || "").trim();
  if (explicit) return explicit;
  const candidate = join(
    resolveDshHome(),
    "profiles",
    "node_modules",
    "@deepseek-ai",
    "dsh",
    "lib",
    "bin.js",
  );
  if (existsSync(candidate)) return candidate;
  throw new Error(
    "DSH_BIN is not configured and no installed @deepseek-ai/dsh entrypoint was found at " +
      `${candidate}. Set DSH_BIN to the absolute path of the DSH lib/bin.js.`,
  );
}

/** Read the local Bridge bootstrap token, if the operator configured one. */
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

function isTransientError(error) {
  const text = String(error?.message || error || "").toLowerCase();
  return /overloaded|rate.?limited|\b429\b|\b503\b|provider temporarily unavailable|timeout|timed out/.test(text);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
    "- Immediately write/overwrite the mandatory artifact from evidence already known to you or already present in the artifact/workspace.",
    "- If evidence is missing, write NOT VERIFIED / EVIDENCE GAP explicitly instead of investigating.",
    "- Remove/replace AI_TEAM_ARTIFACT_STATUS: in_progress only when the artifact truthfully records findings and gaps.",
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
  return { controller, cleanup: () => parentSignal?.removeEventListener("abort", abort) };
}

function promptFor(task, messages) {
  const followUps = messages
    .map(messageText)
    .filter(Boolean)
    .map((text, index) => `Follow-up ${index + 1}:\n${text}`)
    .join("\n\n");
  return [
    `You are executing task ${task.task_id} in the requested workspace.`,
    `Title: ${task.title}`,
    "",
    task.prompt,
    followUps ? `\nAdditional follow-up messages:\n${followUps}` : "",
    "\nWork directly in the current workspace. Complete the task, create every requested output, and report what you actually changed.",
    "Mandatory text artifacts may already contain an AI_TEAM_ARTIFACT_STATUS: in_progress checkpoint. Update that file as you work, and remove/replace the in_progress marker only when the artifact is decision-quality and ready for Supervisor use.",
    "When a Supervisor follow-up says to converge/finalize, stop starting new exploration and finish the mandatory artifact immediately.",
  ].filter(Boolean).join("\n");
}

/**
 * File-ish paths named in a task prompt.
 *
 * V0.1 hard-coded a single known filename, so a task that produced any other
 * requested output could not report it as an artifact. These candidates are the
 * requested outputs; they are reported when the task actually produced them.
 */
export function promptArtifacts(prompt) {
  const pattern = /(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|html?|xml|pdf|png|jpe?g|svg)/g;
  const found = [];
  for (const match of String(prompt || "").matchAll(pattern)) {
    const candidate = match[0].replace(/^\.\//, "");
    if (!found.includes(candidate)) found.push(candidate);
  }
  return found;
}

/** Outputs a task must produce; a missing one fails the task. */
export function requiredArtifacts(task) {
  const required = [];
  if (Array.isArray(task.metadata?.expected_artifacts)) {
    for (const item of task.metadata.expected_artifacts) {
      const value = String(item || "").trim();
      if (value && !required.includes(value)) required.push(value);
    }
  }
  const logicalTaskId = task.metadata?.logical_task_id || task.task_id;
  if (logicalTaskId === "CN-DSH-001") {
    const known = "docs/research/china-social/zhihu-acquisition.md";
    if (!required.includes(known)) required.push(known);
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

/** All artifact paths to look for: required outputs plus prompt-named files. */
export function expectedArtifacts(task) {
  const candidates = [...requiredArtifacts(task)];
  for (const candidate of promptArtifacts(task.prompt)) {
    if (!candidates.includes(candidate)) candidates.push(candidate);
  }
  return candidates;
}

export async function runDshPrompt({
  task,
  prompt,
  signal,
  dshHome = resolveDshHome(),
  timeoutMs = DSH_TASK_TIMEOUT_MS,
}) {
  const dshBin = resolveDshBin();
  const child = spawn(NODE_BIN, [dshBin, "--profile", DSH_PROFILE, prompt], {
    cwd: task.workspace,
    env: { ...process.env, DSH_HOME: dshHome },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  let timedOut = false;
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  const abort = () => child.kill("SIGTERM");
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill("SIGTERM");
  }, Math.max(1, Number(timeoutMs) || DSH_TASK_TIMEOUT_MS));

  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    if (signal?.aborted) {
      const error = new Error("DSH task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`DSH timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) {
      throw new Error(`DSH exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`);
    }
    return { stdout, stderr };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

async function runWithTransientPolicy({ task, prompt, signal, timeoutMs, maxAttempts = 2 }) {
  if (resolveDshModelBackend() === "international_plugin") {
    // 每轮模型请求（包括追问/总结）都必须重新做国际账号 + 当期免费检查。
    // 不重试其他模型，不通过国内版 CLI 回退。
    await assertInternationalFreeEligible();
    return runDshPrompt({ task, prompt, signal, timeoutMs });
  }
  let lastError;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      return await runDshPrompt({ task, prompt, signal, timeoutMs });
    } catch (error) {
      lastError = error;
      if (!isTransientError(error) || signal.aborted) throw error;
      await sleep(500 * (attempt + 1));
    }
  }
  if (process.env.DSH_FALLBACK_HOME && process.env.DSH_FALLBACK_HOME !== resolveDshHome()) {
    return runDshPrompt({ task, prompt, signal, dshHome: process.env.DSH_FALLBACK_HOME, timeoutMs });
  }
  throw lastError;
}

/** 默认国际站插件；必须显式指定 native 才能使用可能计费的原生 DSH。 */
export function resolveDshModelBackend(env = process.env) {
  const value = String(env.DSH_MODEL_BACKEND || "international_plugin").trim().toLowerCase();
  if (!["international_plugin", "native"].includes(value)) {
    throw new Error("DSH_MODEL_BACKEND must be international_plugin or native");
  }
  return value;
}

export const DSH_FREE_MODEL = DSH_INTL_MODEL;

/** Model preflight using the same real DSH runner as formal tasks. */
export async function preflightModel({ timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS } = {}) {
  return runModelPreflight({
    timeoutMs,
    runPrompt: ({ prompt, signal }) =>
      resolveDshModelBackend() === "international_plugin"
        ? (async () => {
          ensureInternationalElectronHelper();
          // 仅当本次进程获显式授权时，才进行一次不含 prompt 的目录刷新。
          // 默认不触发第三方 API，随后仍必须经过原始费用/账号双门禁。
          await refreshBeforePreflightIfApproved();
          await assertInternationalFreeEligible();
          return runDshPrompt({
          task: {
            task_id: "dsh-international-preflight",
            title: "DSH WorkBuddy AI free DeepSeek preflight",
            workspace: process.env.DSH_FREE_PREFLIGHT_DIR || process.cwd(),
            prompt,
          },
          prompt, signal, timeoutMs,
        });
        })()
        : runDshPrompt({
        task: {
          task_id: "dsh-model-preflight",
          title: "DSH model preflight",
          workspace: process.cwd(),
          prompt,
        },
        prompt,
        signal,
      }),
  });
}

/**
 * Report the artifacts a finished task produced.
 *
 * Required outputs (declared in metadata or known for a logical task) must
 * exist or the task fails. Prompt-named files are reported when present, so a
 * task that produces any requested output can return it.
 */
export async function reportArtifacts({ task, addArtifact }) {
  const workspace = task.workspace;
  for (const relativePath of requiredArtifacts(task)) {
    try {
      await access(`${workspace}/${relativePath}`);
    } catch {
      throw new Error(`Expected artifact was not created: ${relativePath}`);
    }
    await assertArtifactReady(workspace, relativePath);
  }

  const artifacts = [];
  for (const relativePath of expectedArtifacts(task)) {
    try {
      await access(`${workspace}/${relativePath}`);
    } catch {
      continue;
    }
    await addArtifact({ path: relativePath, kind: "file", label: relativePath });
    artifacts.push({ path: relativePath, kind: "file", label: relativePath });
  }
  return artifacts;
}

export function createTaskHandler({ runPrompt = runWithTransientPolicy } = {}) {
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
    const runTurn = async ({ messages = [], finalize = false } = {}) => {
      const budget = executionBudgetFor(task, finalize ? "finalization" : "exploration");
      const linked = linkedTurnController(ctx.signal);
      activeTurnController = linked.controller;
      try {
        if (resolveDshModelBackend() === "international_plugin" &&
            typeof ctx.reportProgress === "function") {
          await ctx.reportProgress({
            stage: "international_free_route",
            message: "DSH → WorkBuddy AI international plugin / DeepSeek V4.1 Flash; zero-price gate active; native fallback disabled",
            current: 2, total: 5,
          });
        }
        await runPrompt({
          task,
          prompt: finalize ? finalizePromptFor(task, messages) : promptFor(task, messages),
          signal: linked.controller.signal,
          // DSH CLI 暂无可验证的工具次数硬限制，因此这里硬隔离时间预算。
          timeoutMs: budget.timeoutMs ?? (
            finalize ? Number(process.env.DSH_FINALIZE_TIMEOUT_MS || 60_000) : undefined
          ),
          maxAttempts: finalize ? 1 : 2,
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
      if (typeof ctx.reportProgress === "function") {
        await ctx.reportProgress({
          stage: "artifact_initialized",
          message: initialized.length ? `Initialized ${initialized.length} mandatory artifact checkpoint(s)` : "No checkpointable mandatory artifact needed",
          current: 1,
          total: 5,
        });
      }
      const first = await runTurn({ messages: ctx.getMessages() });
      await ctx.refreshMessages();
      if (first.interruptedForFinalize || finalizeRequested) {
        const followUps = pending.splice(0, pending.length);
        if (typeof ctx.reportProgress === "function") {
          await ctx.reportProgress({ stage: "forced_finalization", message: "DSH exploration interrupted; entering bounded finalization", current: 3, total: 5 });
        }
        finalizeRequested = false;
        finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: true });
        await ctx.refreshMessages();
      }
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        // 收尾开始后不允许重新进入探索模式。
        const needsFinalize = finalizationStarted || followUps.some(isFinalizeMessage);
        if (needsFinalize && typeof ctx.reportProgress === "function") {
          await ctx.reportProgress({ stage: "forced_finalization", message: "DSH entering bounded finalization", current: 3, total: 5 });
        }
        finalizeRequested = false;
        if (needsFinalize) finalizationStarted = true;
        await runTurn({ messages: followUps, finalize: needsFinalize });
        await ctx.refreshMessages();
      }

      const artifacts = await reportArtifacts({
        task,
        addArtifact: (artifact) => ctx.addArtifact(artifact),
      });
      if (typeof ctx.reportProgress === "function") {
        await ctx.reportProgress({ stage: "artifacts", message: `Validated ${artifacts.length} artifact(s)`, current: 5, total: 5 });
      }
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
    agentId: "dsh",
    label: "DSH",
    capabilities: ["implementation", "testing", "debugging", "git"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

/**
 * Startup path used by the process entrypoint.
 *
 * The bootstrap token is required: readiness reporting is authenticated, so a
 * missing token is a configuration error that must fail before the readiness
 * loop rather than retry rejected reports forever.
 */
export async function startDshFromEnvironment({
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
  return startDshWorker({
    client,
    bootstrapToken: token,
    preflight,
    workerFactory,
    ...rest,
  });
}

/** 免费门禁监控与 Worker 生命周期绑定：成功启动不可取消监控。 */
export async function startWithFreeWatch({ start, stopWatch }) {
  try {
    const result = await start();
    if (!result?.started) stopWatch();
    return result;
  } catch (error) {
    stopWatch();
    throw error;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({
    baseUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
  });
  let activeWorker;
  // 每十分钟核查国际站免费资格，过期/改价时报告不可用并停止派工。
  const freeWatch = setInterval(async () => {
    if (resolveDshModelBackend() !== "international_plugin") return;
    try {
      await assertInternationalFreeEligible();
    } catch (error) {
      clearInterval(freeWatch);
      await client.reportReadiness({
        agentId: "dsh",
        modelAvailable: false,
        reason: "international_free_guard_failed",
      }).catch(() => {});
      await activeWorker?.stop();
      console.error("DSH international free eligibility lost:", error.reason || "unknown");
      process.exitCode = 1;
      const deadline = setTimeout(() => process.exit(1), 3000);
      deadline.unref?.();
    }
  }, 10 * 60_000);
  try {
    const result = await startWithFreeWatch({
      start: () => startDshFromEnvironment({
        client,
        workerFactory: () => (activeWorker = createWorker()),
      }),
      stopWatch: () => clearInterval(freeWatch),
    });
    if (!result.started) process.exit(1);
  } catch (error) {
    clearInterval(freeWatch);
    console.error(`DSH startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
