// 仅修复隔离的 DSH 0.2.0-rc.2 Headless：插件异步注册模型晚于 Agent 创建的竞态。
// 不修改桌面版、全局旧 DSH 或任何模型/计费元数据。
import { readFile, writeFile, copyFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";

const target = join(
  homedir(), ".local/state/ai-team/dsh-intl-runtime/node_modules",
  "@deepseek-ai/dsh-headless/lib/index.js",
);
const corePackage = join(
  homedir(), ".local/state/ai-team/dsh-intl-runtime/node_modules",
  "@deepseek-ai/dsh/package.json",
);
const runtime = JSON.parse(await readFile(corePackage, "utf8"));
if (runtime.version !== "0.2.0-rc.2") {
  throw new Error("ISOLATED_DSH_CORE_VERSION_CHANGED_MANUAL_REVIEW_REQUIRED");
}
const marker = "AI_TEAM_WAIT_INTERNATIONAL_CATALOG";
const needle = "\tconst selection = defaultModel.currentSelection();";
const replacement = [
  needle,
  "\t// AI_TEAM_WAIT_INTERNATIONAL_CATALOG: 等待国际版插件完成异步模型注册。",
  '\tif (selection.provider === "workbuddy-ai" && selection.model === "deepseek-v4.1-flash") {',
  '\t\tconst llm = ctx.get("llm");',
  '\t\tconst deadline = Date.now() + 25000;',
  '\t\tlet available = false;',
  '\t\twhile (Date.now() < deadline) {',
  '\t\t\ttry {',
  '\t\t\t\tconst entries = await llm?.listModels("workbuddy-ai");',
  '\t\t\t\tif (entries?.some((model) => model.id === "deepseek-v4.1-flash")) {',
  '\t\t\t\t\tavailable = true;',
  '\t\t\t\t\tbreak;',
  '\t\t\t\t}',
  '\t\t\t} catch { /* 目录在异步初始化阶段尚不可用 */ }',
  '\t\t\tawait new Promise((resolve) => setTimeout(resolve, 250));',
  '\t\t}',
  '\t\tif (!available) throw new Error("AI_TEAM_INTERNATIONAL_FREE_MODEL_CATALOG_NOT_READY");',
  '\t}',
].join("\n");
const original = await readFile(target, "utf8");
if (original.includes(marker)) {
  console.log("DSH_ISOLATED_READINESS_PATCH_ALREADY_PRESENT");
  process.exit(0);
}
if (original.split(needle).length !== 2) {
  throw new Error("DSH_HEADLESS_VERSION_MISMATCH_REFUSING_PATCH");
}
const backup = target + ".before-ai-team-model-wait.bak";
try {
  await readFile(backup);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  await copyFile(target, backup);
}
await writeFile(target, original.replace(needle, replacement));
console.log("DSH_ISOLATED_READINESS_PATCH_APPLIED");
