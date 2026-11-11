import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
  READINESS_PROMPT,
  requireBootstrapToken,
  runModelPreflight,
  startMuseWorker,
} from "./readiness.js";

const MUSE_TASK_TIMEOUT_MS = Number(process.env.MUSE_TASK_TIMEOUT_MS || 1_800_000);
const DEFAULT_MODEL = "muse-spark-1.3";
const DEFAULT_REASONING_EFFORT = "high";
const DEFAULT_MAX_MODEL_STEPS = "80";

export function resolveMuseBin() {
  return (process.env.MUSE_BIN || "muse").trim();
}

export function resolveMuseModel() {
  return (process.env.MUSE_MODEL || DEFAULT_MODEL).trim();
}

export function resolveMuseReasoningEffort() {
  return (process.env.MUSE_REASONING_EFFORT || DEFAULT_REASONING_EFFORT).trim();
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
    "Use tools when needed and validate deliverables before finishing.",
    "Do not invent successful tool calls, observations, or files.",
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

export function buildMuseArgs({
  prompt,
  workspace,
  outputJson = true,
  model = resolveMuseModel(),
  reasoningEffort = resolveMuseReasoningEffort(),
} = {}) {
  const args = [
    "exec",
    "--workspace", workspace,
    "--trust-workspace",
    "--disable-approval",
    "--model", model,
    "--reasoning-effort", reasoningEffort,
    "--max-model-steps", process.env.MUSE_MAX_MODEL_STEPS || DEFAULT_MAX_MODEL_STEPS,
  ];
  if (outputJson) args.push("--json");
  if (process.env.MUSE_DISABLE_WEB_TOOLS === "1") args.push("--disable-web-tools");
  args.push(prompt);
  return args;
}

export async function runMusePrompt({
  task,
  prompt,
  signal,
  outputJson = true,
  onEvent = async () => {},
  museBin = resolveMuseBin(),
  timeoutMs = MUSE_TASK_TIMEOUT_MS,
} = {}) {
  const child = spawn(
    museBin,
    buildMuseArgs({ prompt, workspace: task.workspace, outputJson }),
    {
      cwd: task.workspace,
      env: { ...process.env, NO_COLOR: "1", TERM: "dumb" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let stdout = "";
  let stderr = "";
  let timedOut = false;
  const events = [];

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const abort = () => child.kill("SIGTERM");
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill("SIGTERM");
  }, Math.max(1, Number(timeoutMs) || MUSE_TASK_TIMEOUT_MS));

  const output = (async () => {
    child.stdout.setEncoding("utf8");
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    for await (const line of lines) {
      stdout += line + "\n";
      const trimmed = line.trim();
      if (!trimmed || !outputJson) continue;
      try {
        const event = JSON.parse(trimmed);
        events.push(event);
        await onEvent(event);
      } catch {
        // Keep non-JSON output for diagnostics. Exit status remains authoritative.
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
      const error = new Error("Muse task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (timedOut) {
      const error = new Error(`Muse timed out after ${timeoutMs}ms`);
      error.code = "timeout";
      throw error;
    }
    if (code !== 0) {
      throw new Error(
        `Muse exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`,
      );
    }
    return { stdout, stderr, events };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

function flattenEvent(event) {
  const values = [event];
  for (const key of ["event", "payload", "data", "message", "update", "params"]) {
    if (event?.[key] && typeof event[key] === "object") values.push(event[key]);
  }
  return values;
}

export function museProgressFromEvent(event) {
  const flattened = flattenEvent(event);
  const serialized = JSON.stringify(event || {}).toLowerCase();
  const type = flattened
    .map((item) => String(item?.type ?? item?.kind ?? item?.event_type ?? item?.name ?? ""))
    .find(Boolean)
    ?.toLowerCase() || "";
  const toolish = type.includes("tool") ||
    serialized.includes("tool_call") ||
    serialized.includes("tooluse") ||
    serialized.includes("tool_use");
  if (toolish) {
    let name = "";
    for (const item of flattened) {
      name = item?.tool_name ?? item?.toolName ?? item?.name ?? item?.tool?.name ?? "";
      if (name) break;
    }
    return { stage: "tool_use", message: `Muse is using ${String(name || "tool")}` };
  }
  if (
    type.includes("assistant") ||
    type.includes("model") ||
    type.includes("session") ||
    serialized.includes("assistant_message")
  ) {
    return {
      stage: "model_session",
      message: `Muse session is active (${resolveMuseModel()})`,
      current: 2,
      total: 4,
    };
  }
  return null;
}

export function finalTextFromEvents(events, stdout = "") {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    for (const candidate of [
      event?.response,
      event?.result,
      event?.text,
      event?.message?.content,
      event?.message?.text,
      event?.data?.text,
      event?.data?.content,
    ]) {
      if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
    }
  }
  return String(stdout || "").trim();
}

async function maybeProgress(ctx, progress) {
  if (typeof ctx.reportProgress === "function") await ctx.reportProgress(progress);
}

export async function collectArtifacts(task) {
  for (const relativePath of requiredArtifacts(task)) {
    try {
      await access(`${task.workspace}/${relativePath}`);
    } catch {
      throw new Error(`Expected artifact was not created: ${relativePath}`);
    }
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

export function createTaskHandler({ runPrompt = runMusePrompt } = {}) {
  return async function handleTask(ctx) {
    const task = ctx.task;
    const pending = [];
    let sessionReported = false;
    const unsubscribe = ctx.onMessage((message) => pending.push(message));
    const onEvent = async (event) => {
      const progress = museProgressFromEvent(event);
      if (!progress) return;
      if (progress.stage === "model_session") {
        if (sessionReported) return;
        sessionReported = true;
      }
      await maybeProgress(ctx, progress);
    };
    try {
      await maybeProgress(ctx, {
        stage: "preparing",
        message: "Preparing Muse Code headless session",
        current: 1,
        total: 4,
      });
      await runPrompt({
        task,
        prompt: promptFor(task, ctx.getMessages()),
        signal: ctx.signal,
        outputJson: true,
        onEvent,
      });
      if (!sessionReported) {
        sessionReported = true;
        await maybeProgress(ctx, {
          stage: "model_session",
          message: `Muse session completed model work (${resolveMuseModel()})`,
          current: 2,
          total: 4,
        });
      }
      await ctx.refreshMessages();
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        await runPrompt({
          task,
          prompt: promptFor(task, followUps),
          signal: ctx.signal,
          outputJson: true,
          onEvent,
        });
        await ctx.refreshMessages();
      }
      await maybeProgress(ctx, {
        stage: "finalizing",
        message: "Muse produced a final result",
        current: 3,
        total: 4,
      });
      const artifacts = await collectArtifacts(task);
      await maybeProgress(ctx, {
        stage: "artifacts",
        message: `Validated ${artifacts.length} artifact(s)`,
        current: 4,
        total: 4,
      });
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
    agentId: "muse",
    label: "Muse Code",
    capabilities: ["implementation", "long_context", "multimodal", "code_review"],
    ...options,
    onTask: options.onTask ?? handleTask,
  });
}

export async function preflightModel({
  timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS,
  runPrompt = runMusePrompt,
} = {}) {
  return runModelPreflight({
    timeoutMs,
    runPrompt: async ({ signal }) => {
      const result = await runPrompt({
        task: {
          task_id: "muse-model-preflight",
          title: "Muse model preflight",
          workspace: process.cwd(),
          prompt: READINESS_PROMPT,
        },
        prompt: READINESS_PROMPT,
        signal,
        outputJson: true,
      });
      return {
        stdout: result.stdout,
        response: finalTextFromEvents(result.events, result.stdout),
      };
    },
  });
}

export async function startMuseFromEnvironment({
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
  return startMuseWorker({
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
    const result = await startMuseFromEnvironment({ client });
    if (!result.started) process.exit(1);
  } catch (error) {
    console.error(`Muse startup failed (${error.code || "error"}): ${error.message}`);
    process.exit(1);
  }
}
