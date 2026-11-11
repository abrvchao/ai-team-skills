// 仅本地测试：从本机受限凭据文件读取 Key，不输出 Key 或完整 API 响应。
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
const id = process.argv[2];
if (!["mimo", "nvidia"].includes(id)) throw new Error("invalid provider");
const root = resolve(fileURLToPath(new URL("../../", import.meta.url)));
process.env.MODEL_WORKER_ID = id;
process.env.MODEL_API_KEY_FILE = resolve(root, ".orchestrator", "model-keys", id + ".key");
try {
  const { callModel } = await import("../src/index.js");
  const response = await callModel("请只回复 MODEL_OK", { maxTokens: 96 });
  console.log(JSON.stringify({ provider: id, ok: Boolean(response.text.trim()), tokens: response.usage?.total_tokens ?? null }));
} catch (error) {
  console.log(JSON.stringify({ provider: id, ok: false, reason: String(error.message).slice(0, 60) }));
  process.exitCode = 1;
}
