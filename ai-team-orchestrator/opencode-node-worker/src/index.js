// OpenCode Worker：plan（只读计划）+ pure（禁用插件）+ 免费 MiMo。
// 此版本只承接研究/审查/综合，不允许执行实施任务或写产品源代码。
import { spawn } from "node:child_process";
import { readFile, mkdir, writeFile, realpath } from "node:fs/promises";
import { resolve, dirname, sep, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";
import {
  DEFAULT_MODEL, FREE_MODEL_ROSTER, requireAllowlistedModel,
  requireCurrentFreeModel, resolveTaskModel,
} from "./free-model-gate.js";

export const AGENT_ID = "opencode";
export const MODEL = DEFAULT_MODEL;
export const PREFLIGHT_TOKEN = "OPENCODE_WORKER_OK";
const ALLOWED_PHASES = new Set(["primary", "review", "synthesis"]);

export function buildArgs(prompt, { model = MODEL } = {}) {
  requireAllowlistedModel(model);
  // --auto 不可用；此处明确只启用 plan + pure。
  return ["run", "--pure", "--agent", "plan", "--model", model,
          // 固定标题，避免附加的自动会话命名模型调用。
          "--title", "AI Team offline review", "--format", "json", String(prompt)];
}

export function parseEvents(stdout) {
  const events = [];
  for (const line of String(stdout || "").split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const value = JSON.parse(line);
      if (value && typeof value === "object") events.push(value);
    } catch {
      // 非 JSON 控制台日志绝不能成为模型正文。
    }
  }
  return events;
}

export function extractText(events) {
  return events
    .filter((event) => event?.type === "text" && typeof event?.part?.text === "string")
    .map((event) => event.part.text)
    .join("").trim();
}

export function artifactName(task) {
  const phase = String(task?.metadata?.team_phase || "review");
  if (!ALLOWED_PHASES.has(phase)) throw new Error("opencode_read_only_phase_required");
  const requested = task?.metadata?.expected_artifacts;
  const fallback = { primary: "primary.md", review: "review.md", synthesis: "final.md" };
  const name = Array.isArray(requested) && requested.length === 1 &&
               typeof requested[0] === "string" ? requested[0] : fallback[phase];
  if (!name || isAbsolute(name) || name.split(/[\\/]/).includes("..") ||
      !/\.(md|txt)$/i.test(name)) throw new Error("opencode_artifact_path_not_allowed");
  return name;
}

export async function persistArtifact(task, text) {
  const content = String(text || "").trim();
  if (!content || content.length > 100_000) throw new Error("opencode_empty_or_oversize_output");
  const root = await realpath(resolve(String(task.workspace || "")));
  const name = artifactName(task);
  const destination = resolve(root, name);
  if (!destination.startsWith(root + sep)) throw new Error("opencode_workspace_escape");
  await mkdir(dirname(destination), { recursive: true });
  const parent = await realpath(dirname(destination));
  if (parent !== root && !parent.startsWith(root + sep)) throw new Error("opencode_symlink_escape");
  // wx 拒绝覆盖已存在的文件，尤其拒绝通过 symlink 写到工作区外部。
  await writeFile(destination, content + "\n", { encoding: "utf8", flag: "wx", mode: 0o600 });
  return name;
}

export async function runOpenCode(prompt, {
  workspace = process.cwd(), signal, timeoutMs = 100_000,
  bin = process.env.OPENCODE_BIN || "/opt/homebrew/bin/opencode",
  model = MODEL, priceGate = requireCurrentFreeModel,
  liveApproved = process.env.AI_TEAM_OPENCODE_LIVE_APPROVED === "1",
} = {}) {
  // Bridge 注册/模型缓存的存在不构成推理授权；无人工批准时直接拒绝。
  if (liveApproved !== true) throw new Error("opencode_live_model_not_authorized");
  requireAllowlistedModel(model);
  if (String(prompt).length > 12_000) throw new Error("opencode_prompt_too_large");
  await priceGate(model);
  return new Promise((resolveRun, rejectRun) => {
    const env = { ...process.env };
    delete env.SERVER__PORT; delete env.SERVER__HOST; delete env.NODE_OPTIONS;
    const child = spawn(bin, buildArgs(prompt, { model }), {
      cwd: workspace, env, stdio: ["ignore", "pipe", "pipe"],
      detached: process.platform !== "win32",
    });
    let stdout = ""; let done = false; let timeoutHit = false;
    // CLI 可能派生后代进程，超时时必须清理同一进程组，避免孤儿继续调用模型。
    const killGroup = (signalName) => {
      try {
        if (process.platform !== "win32" && child.pid) process.kill(-child.pid, signalName);
        else child.kill(signalName);
      } catch (error) { if (error.code !== "ESRCH") throw error; }
    };
    const stop = () => {
      killGroup("SIGTERM");
      const force = setTimeout(() => killGroup("SIGKILL"), 3000);
      force.unref?.();
    };
    const abort = () => stop();
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => { timeoutHit = true; stop(); },
      Math.max(1, Math.min(Number(timeoutMs) || 100_000, 120_000)));
    const settle = (err, value) => {
      if (done) return;
      done = true; clearTimeout(timer); signal?.removeEventListener("abort", abort);
      if (err) rejectRun(err); else resolveRun(value);
    };
    child.stdout.on("data", (data) => { stdout = (stdout + data.toString()).slice(-512_000); });
    child.stderr.on("data", () => {}); // 丢弃可能含凭据的模型错误原文。
    child.on("error", () => settle(new Error("opencode_spawn_failed")));
    child.on("close", (code) => {
      if (signal?.aborted) return settle(new Error("opencode_cancelled"));
      if (timeoutHit) return settle(new Error("opencode_timeout"));
      if (code !== 0) return settle(new Error("opencode_cli_failed"));
      const events = parseEvents(stdout);
      if (events.some((event) => event?.type === "error" || event?.part?.type === "error")) {
        return settle(new Error("opencode_model_error"));
      }
      const text = extractText(events);
      if (!text) return settle(new Error("opencode_empty_result"));
      settle(null, { text, eventCount: events.length });
    });
  });
}

export async function preflight({ run = runOpenCode } = {}) {
  const model = resolveTaskModel(null);
  const reply = await run("Only reply OPENCODE_WORKER_OK. No tools.", {
    workspace: process.env.OPENCODE_PREFLIGHT_DIR || process.cwd(),
    timeoutMs: 65000,
    model,
  });
  if (reply.text !== PREFLIGHT_TOKEN) throw new Error("opencode_preflight_mismatch");
  return true;
}

export async function handleTask(ctx, { run = runOpenCode } = {}) {
  const task = ctx.task;
  const name = artifactName(task);
  const model = resolveTaskModel(task);
  await ctx.reportProgress?.({ stage: "preparing", message: `OpenCode read-only / ${model}`, current: 1, total: 3 });
  const messages = (await ctx.getMessages?.() || [])
    .map((msg) => String(msg.content || msg.text || "")).filter(Boolean).join("\n").slice(0, 5000);
  const prompt = [
    "You are a read-only AI Team reviewer. Do not call tools or edit files.",
    "Return complete Markdown content; the adapter persists the output.",
    "Distinguish verified facts from supplied documents and assumptions; never fabricate tests.",
    "Task: " + String(task.title || ""),
    "Objective: " + String(task.prompt || ""),
    "Latest instructions: " + messages,
  ].join("\n\n");
  const response = await run(prompt, { workspace: task.workspace, signal: ctx.signal, model });
  await ctx.reportProgress?.({ stage: "model", message: "Model returned text", current: 2, total: 3 });
  await persistArtifact(task, response.text);
  await ctx.reportProgress?.({ stage: "artifact", message: "Text artifact validated", current: 3, total: 3 });
  return { artifacts: [{ path: name, kind: "file", label: name }] };
}

export async function startFromEnvironment() {
  const file = process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE;
  if (!file) throw new Error("opencode_bridge_token_file_required");
  const bootstrapToken = (await readFile(file, "utf8")).trim();
  if (!bootstrapToken) throw new Error("opencode_bridge_token_missing");
  const bridgeUrl = process.env.AI_TEAM_BRIDGE_URL || "http://127.0.0.1:8765";
  const client = new BridgeClient({ baseUrl: bridgeUrl });
  try {
    await preflight();
    await client.reportReadiness({ agentId: AGENT_ID, modelAvailable: true, bootstrapToken });
  } catch (error) {
    await client.reportReadiness({ agentId: AGENT_ID, modelAvailable: false,
      reason: "model_unavailable", bootstrapToken }).catch(() => {});
    throw error;
  }
  const worker = new AgentWorker({
    bridgeUrl, agentId: AGENT_ID,
    label: "OpenCode / MiMo Free (Read Only)",
    capabilities: ["research", "code_review", "planning"], onTask: handleTask,
  });
  await worker.start();
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  startFromEnvironment().catch((error) => {
    console.error("OpenCode startup error:", String(error?.message || "unknown"));
    process.exitCode = 1;
  });
}
