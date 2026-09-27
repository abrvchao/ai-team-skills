import { spawn } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import {
  DEFAULT_PREFLIGHT_TIMEOUT_MS,
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
  ].filter(Boolean).join("\n");
}

function expectedArtifacts(task) {
  const logicalTaskId = task.metadata?.logical_task_id || task.task_id;
  if (logicalTaskId === "CN-DSH-001") {
    return ["docs/research/china-social/zhihu-acquisition.md"];
  }
  if (/dsh-online-proof\.txt/i.test(task.prompt)) return ["dsh-online-proof.txt"];
  return [];
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

async function runWithTransientPolicy({ task, prompt, signal }) {
  let lastError;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await runDshPrompt({ task, prompt, signal });
    } catch (error) {
      lastError = error;
      if (!isTransientError(error) || signal.aborted) throw error;
      await sleep(500 * (attempt + 1));
    }
  }
  if (process.env.DSH_FALLBACK_HOME && process.env.DSH_FALLBACK_HOME !== resolveDshHome()) {
    return runDshPrompt({ task, prompt, signal, dshHome: process.env.DSH_FALLBACK_HOME });
  }
  throw lastError;
}

/** Model preflight using the same real DSH runner as formal tasks. */
export async function preflightModel({ timeoutMs = DEFAULT_PREFLIGHT_TIMEOUT_MS } = {}) {
  return runModelPreflight({
    timeoutMs,
    runPrompt: ({ prompt, signal }) =>
      runDshPrompt({
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

export async function handleTask(ctx) {
  const task = ctx.task;
  const messages = ctx.getMessages();
  const pending = [];
  const unsubscribe = ctx.onMessage((message) => pending.push(message));
  try {
    await runWithTransientPolicy({ task, prompt: promptFor(task, messages), signal: ctx.signal });
    await ctx.refreshMessages();
    while (pending.length > 0) {
      const followUp = pending.splice(0, pending.length);
      await runWithTransientPolicy({
        task,
        prompt: promptFor(task, followUp),
        signal: ctx.signal,
      });
      await ctx.refreshMessages();
    }

    const artifacts = [];
    for (const relativePath of expectedArtifacts(task)) {
      try {
        await access(`${task.workspace}/${relativePath}`);
        await ctx.addArtifact({
          path: relativePath,
          kind: "file",
          label: relativePath,
        });
        artifacts.push({ path: relativePath, kind: "file", label: relativePath });
      } catch {
        throw new Error(`Expected artifact was not created: ${relativePath}`);
      }
    }
    return { artifacts };
  } finally {
    unsubscribe();
  }
}

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

if (import.meta.url === `file://${process.argv[1]}`) {
  const client = new BridgeClient({
    baseUrl: process.env.AI_TEAM_BRIDGE_URL ?? "http://127.0.0.1:8765",
  });
  const bootstrapToken = await resolveBootstrapToken();
  if (!bootstrapToken) {
    console.error(
      "DSH readiness: no bootstrap token configured; model health will not be reported to the Bridge. " +
        "Set AI_TEAM_BOOTSTRAP_TOKEN or AI_TEAM_BOOTSTRAP_TOKEN_FILE.",
    );
  }
  client.bootstrapToken = bootstrapToken;
  const result = await startDshWorker({
    client,
    preflight: () => preflightModel(),
    workerFactory: () => createWorker(),
  });
  if (!result.started) process.exit(1);
}
