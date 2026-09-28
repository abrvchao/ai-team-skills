import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
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
    process.env.GEMINI_APPROVAL_MODE || "auto_edit",
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
} = {}) {
  const child = spawn(geminiBin, buildGeminiArgs(prompt, outputFormat), {
    cwd: task.workspace,
    env: { ...process.env },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stderr = "";
  let stdout = "";
  let timedOut = false;
  const events = [];

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });

  const abort = () => child.kill("SIGTERM");
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill("SIGTERM");
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
    const unsubscribe = ctx.onMessage((message) => pending.push(message));
    try {
      await maybeProgress(ctx, {
        stage: "preparing",
        message: "Preparing Gemini headless session",
        current: 1,
        total: 4,
      });

      const onEvent = async (event) => {
        if (event?.type === "init") {
          await maybeProgress(ctx, {
            stage: "model_session",
            message: `Gemini session initialized${event.model ? ` (${event.model})` : ""}`,
            current: 2,
            total: 4,
          });
        } else if (event?.type === "tool_use") {
          const name = event.tool_name || event.name || "tool";
          await maybeProgress(ctx, {
            stage: "tool_use",
            message: `Gemini is using ${name}`,
          });
        } else if (event?.type === "result") {
          await maybeProgress(ctx, {
            stage: "finalizing",
            message: "Gemini produced a final result",
            current: 3,
            total: 4,
          });
        }
      };

      await runPrompt({
        task,
        prompt: promptFor(task, ctx.getMessages()),
        signal: ctx.signal,
        outputFormat: "stream-json",
        onEvent,
      });

      await ctx.refreshMessages();
      while (pending.length > 0) {
        const followUps = pending.splice(0, pending.length);
        await runPrompt({
          task,
          prompt: promptFor(task, followUps),
          signal: ctx.signal,
          outputFormat: "stream-json",
          onEvent,
        });
        await ctx.refreshMessages();
      }

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
