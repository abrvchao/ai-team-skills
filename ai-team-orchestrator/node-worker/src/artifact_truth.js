import { readFile } from "node:fs/promises";

export const ARTIFACT_IN_PROGRESS_MARKER = "AI_TEAM_ARTIFACT_STATUS: in_progress";

const TEXT_LIKE_EXTENSIONS = new Set([
  ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv", ".html", ".xml",
]);

function extensionOf(path = "") {
  const value = String(path || "");
  const slash = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"));
  const base = value.slice(slash + 1);
  const dot = base.lastIndexOf(".");
  return dot >= 0 ? base.slice(dot).toLowerCase() : "";
}

/**
 * Required artifact truth gate（必需成果真相门槛）.
 *
 * 文件存在并不等于成果完成。Node Worker 会预先创建 Markdown checkpoint；
 * 如果最终仍保留 in_progress 标记，任务必须失败，不能注册成 completed artifact。
 */
export async function assertArtifactReady(workspace, relativePath) {
  const ext = extensionOf(relativePath);
  if (!TEXT_LIKE_EXTENSIONS.has(ext)) return;
  const absolute = workspace + "/" + relativePath;
  let text;
  try {
    text = await readFile(absolute, "utf8");
  } catch {
    // 文件存在性由调用方原有检查负责；这里不改变 missing-artifact 错误语义。
    return;
  }
  if (String(text).includes(ARTIFACT_IN_PROGRESS_MARKER)) {
    throw new Error("Expected artifact is still an in-progress checkpoint: " + relativePath);
  }
}
