// 中文说明：NVIDIA NIM / 小米 MiMo 共用研究型 Worker，不给模型执行命令的权限。
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve, relative, isAbsolute, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { AgentWorker, BridgeClient } from "../../node-worker/src/index.js";

const PROVIDERS = {
  mimo: { url: "https://api.xiaomimimo.com/v1/chat/completions", model: "mimo-v2.6-flash" },
  nvidia: { url: "https://integrate.api.nvidia.com/v1/chat/completions", model: "openai/gpt-oss-20b" },
};
const agentId = process.env.MODEL_WORKER_ID;
if (!PROVIDERS[agentId]) throw new Error("MODEL_WORKER_ID must be mimo or nvidia");
const config = PROVIDERS[agentId];
const readKey = async () => (await readFile(process.env.MODEL_API_KEY_FILE, "utf8")).trim();
const token = async () => (await readFile(process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE, "utf8")).trim();
const endpoint = process.env.MODEL_API_URL || config.url;
const model = process.env.MODEL_NAME || config.model;

const execFileAsync = promisify(execFile);
const COST_GATE_SCRIPT = fileURLToPath(new URL("../../cost_gate_cli.py", import.meta.url));

export function paidPreflightEnabled() {
  // 用户曾因开机自动预检被反复扣费，因此默认不允许任何远程模型就绪探测。
  return process.env.AI_TEAM_MODEL_PREFLIGHT_OPT_IN === "1";
}

async function reserveModelBudget({ requestId, taskId, signal }) {
  // 启动、任务、重试全部经过同一个跨进程 SQLite 预算账本；缺任一条件即拒绝。
  if (process.env.AI_TEAM_MODEL_CALLS_OPT_IN !== "1") throw new Error("model_paid_calls_disabled");
  const db = process.env.AI_TEAM_COST_LEDGER_DB;
  const price = process.env.AI_TEAM_MAX_CALL_COST_MICROS;
  const currency = process.env.AI_TEAM_COST_CURRENCY;
  if (!db || !/^[1-9]\d*$/.test(price || "") || !["CNY", "USD"].includes(currency)
      || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(requestId || "")
      || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(taskId || "")) {
    throw new Error("model_budget_not_configured");
  }
  try {
    const { stdout } = await execFileAsync(
      process.env.AI_TEAM_COST_GATE_PYTHON || "/opt/homebrew/bin/python3",
      [COST_GATE_SCRIPT, "--db", db, "--provider", agentId,
       "--request-id", requestId, "--task-id", taskId,
       "--currency", currency, "--unit-price-micros", price],
      { timeout: 5000, maxBuffer: 1024, signal },
    );
    if (JSON.parse(stdout)?.allowed !== true) throw new Error("model_budget_denied");
  } catch {
    // 不传播进程参数、数据库路径、原始错误文本或供应商凭据。
    throw new Error("model_budget_denied");
  }
}

export async function callModel(prompt, { signal, maxTokens = 1024, requestId, taskId } = {}) {
  if (signal?.aborted) throw new Error("model_call_aborted");
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 2048) {
    throw new Error("invalid_model_max_tokens");
  }
  // 必须先拿到一次性预算许可，禁止直接通过导出的 callModel 绕开费用检查。
  await reserveModelBudget({ requestId, taskId, signal });
  const key = await readKey();
  if (!key) throw new Error("provider credential missing");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60_000);
  signal?.addEventListener("abort", () => controller.abort(), { once: true });
  try {
    const response = await fetch(endpoint, {
      method: "POST", signal: controller.signal,
      headers: { "Authorization": `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model, stream: false, max_tokens: maxTokens, messages: [
        { role: "system", content: "You are a careful review agent. Respond in Chinese, distinguish verified facts from guesses, never claim to have executed code." },
        { role: "user", content: prompt },
      ] }),
    });
    if (!response.ok) {
      // 中文说明：不记录完整错误正文，防止响应中意外夹带敏感信息。
      throw new Error(`model_http_${response.status}`);
    }
    const json = await response.json();
    const result = json.choices?.[0]?.message?.content;
    const text = typeof result === "string" ? result : Array.isArray(result)
      ? result.map((x) => x.text || "").join("") : "";
    if (!text.trim()) throw new Error("model_empty_response");
    return { text, usage: json.usage || {} };
  } finally { clearTimeout(timer); }
}

export function requiredArtifact(task) {
  const items = task.metadata?.expected_artifacts;
  const named = Array.isArray(items) && typeof items[0] === "string" ? items[0] : "review.md";
  if (isAbsolute(named) || named.split(/[\\/]/).includes("..")) throw new Error("artifact path unsafe");
  const root = resolve(task.workspace), destination = resolve(root, named);
  if (!destination.startsWith(root + sep)) throw new Error("artifact escaped workspace");
  return { named, destination };
}

export async function handleTask(ctx) {
  const { task } = ctx;
  await ctx.reportProgress({ stage: "model", message: "Running bounded model call", current: 1, total: 2 });
  const prompt = `Task title: ${task.title}\nTask instructions:\n${task.prompt || ""}\n\nOnly produce written research/review; no actual filesystem/code access.\nOutput complete Markdown.\n`;
  // 中文说明：单个任务只允许一次 API 调用，避免 Supervisor 多轮收尾导致费用膨胀。
  const taskId = task.task_id || task.id;
  const result = await callModel(prompt, {
    signal: ctx.signal, maxTokens: 1536,
    taskId, requestId: taskId ? `model-${taskId}` : undefined,
  });
  const { named, destination } = requiredArtifact(task);
  await mkdir(resolve(destination, ".."), { recursive: true });
  await writeFile(destination, result.text + "\n", { flag: "w", mode: 0o600 });
  await ctx.reportProgress({ stage: "artifact", message: "Research artifact written", current: 2, total: 2 });
  return { artifacts: [{ path: named, kind: "file", label: named }] };
}

async function main() {
  if (!process.env.MODEL_API_KEY_FILE || !process.env.AI_TEAM_BOOTSTRAP_TOKEN_FILE) throw new Error("key file paths required");
  // 不再随着 launchd/开机自动进行可能扣费的远程预检；必须再次显式开启预检，
  // 且预检还要经过付费调用开关与共享账本，双重防护。
  if (!paidPreflightEnabled()) throw new Error("paid_model_preflight_disabled");
  const probe = await callModel("请只回复 MODEL_OK", {
    maxTokens: 48, taskId: `readiness-${agentId}`,
    requestId: `readiness-${agentId}`,
  });
  if (probe.text.trim() !== "MODEL_OK") throw new Error("model_readiness_mismatch");
  const client = new BridgeClient({ baseUrl: process.env.AI_TEAM_BRIDGE_URL || "http://127.0.0.1:8765" });
  await client.reportReadiness({ agentId, modelAvailable: true, bootstrapToken: await token() });
  const worker = new AgentWorker({
    agentId, label: agentId === "mimo" ? "Xiaomi MiMo" : "NVIDIA NIM",
    capabilities: ["research", "code_review"], onTask: handleTask,
  });
  await worker.start();
}
if (process.argv[1] && import.meta.url === new URL(`file://${resolve(process.argv[1])}`).href) {
  main().catch((e) => { console.error(`${agentId} startup failed: ${e.message}`); process.exitCode = 1; });
}
