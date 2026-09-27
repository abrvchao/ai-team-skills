import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";

const DSH_BIN = process.env.DSH_BIN ||
  "/Users/brvchaoliu/.npm/_npx/1da1392061ab1944/node_modules/@deepseek-ai/dsh/lib/bin.js";
const DSH_HOME = process.env.DSH_HOME || "/Users/brvchaoliu/.dsh";
const DSH_PRIMARY_HOME = process.env.DSH_PRIMARY_HOME || DSH_HOME;
const DSH_FALLBACK_HOME = process.env.DSH_FALLBACK_HOME || "";
const NODE_BIN = process.env.DSH_NODE_BIN || process.execPath;

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

async function runDshPrompt({ task, prompt, signal, dshHome = DSH_PRIMARY_HOME }) {
  const child = spawn(NODE_BIN, [DSH_BIN, "--profile", "headless", prompt], {
    cwd: task.workspace,
    env: { ...process.env, DSH_HOME: dshHome },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  const abort = () => child.kill("SIGTERM");
  if (signal.aborted) abort();
  signal.addEventListener("abort", abort, { once: true });
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", resolve);
    });
    if (signal.aborted) {
      const error = new Error("DSH task cancelled");
      error.name = "AbortError";
      throw error;
    }
    if (code !== 0) {
      throw new Error(`DSH exited with code ${code}: ${(stderr || stdout).trim().slice(-2_000)}`);
    }
    return { stdout, stderr };
  } finally {
    signal.removeEventListener("abort", abort);
  }
}

async function runWithTransientPolicy({ task, prompt, signal }) {
  let lastError;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await runDshPrompt({ task, prompt, signal, dshHome: DSH_PRIMARY_HOME });
    } catch (error) {
      lastError = error;
      if (!isTransientError(error) || signal.aborted) throw error;
      await sleep(500 * (attempt + 1));
    }
  }
  if (DSH_FALLBACK_HOME && DSH_FALLBACK_HOME !== DSH_PRIMARY_HOME) {
    return runDshPrompt({ task, prompt, signal, dshHome: DSH_FALLBACK_HOME });
  }
  throw lastError;
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

export async function preflightModel() {
  const controller = new AbortController();
  return runWithTransientPolicy({
    task: { task_id: "dsh-model-preflight", title: "DSH model preflight", workspace: process.cwd(), prompt: "" },
    prompt: "仅返回：DSH_MODEL_OK",
    signal: controller.signal,
  });
}

export async function waitForModel(client, { retryIntervalMs = 30_000 } = {}) {
  while (true) {
    try {
      await preflightModel();
      await client.reportReadiness({ agentId: "dsh", available: true });
      return;
    } catch (error) {
      await client.reportReadiness({
        agentId: "dsh",
        available: false,
        reason: "model_unavailable",
      }).catch(() => {});
      console.error(`DSH readiness: model_unavailable: ${error.message}`);
      await sleep(retryIntervalMs);
    }
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
  await waitForModel(client);
  const worker = createWorker();
  const stop = () => worker.stop();
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  await worker.start();
}
