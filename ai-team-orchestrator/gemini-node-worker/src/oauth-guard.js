/**
 * AI Team Gemini CLI 账号订阅保护门禁。
 * 只允许 Google 账号 OAuth，不把 Google AI Pro 当成 AI Studio API 额度。
 * 此模块离线，不记录或打印 access/refresh token。
 */
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

export const AUTH_TYPE = "oauth-personal";
export const GEMINI_OAUTH_APPROVAL_FLAG = "AI_TEAM_GEMINI_OAUTH_APPROVED";
export const DEFAULT_SETTINGS_PATH = join(homedir(), ".gemini", "settings.json");
export const DEFAULT_OAUTH_PATH = join(homedir(), ".gemini", "oauth_creds.json");

const forbiddenFallbackVariables = [
  "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI",
  "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT",
  "GOOGLE_CLOUD_LOCATION", "GEMINI_CLI_API_KEY",
];

/** 子进程看不到既有 API Key / Vertex 计费环境变量。 */
export function oauthOnlyEnvironment(env = process.env) {
  const clean = { ...env };
  for (const name of forbiddenFallbackVariables) delete clean[name];
  return clean;
}

export function checkOAuthData(settings, credential, { approved = false } = {}) {
  if (approved !== true) throw new Error("gemini_oauth_model_not_authorized");
  if (settings?.security?.auth?.selectedType !== AUTH_TYPE) {
    throw new Error("gemini_oauth_not_selected");
  }
  if (!credential || typeof credential.refresh_token !== "string" ||
      credential.refresh_token.length < 16) {
    throw new Error("gemini_oauth_login_required");
  }
  return { auth: AUTH_TYPE, refreshCredentialPresent: true };
}

/** 允许过期的 access token 由官方 CLI 刷新；严禁走 API Key。 */
export async function assertGeminiOAuthReady({
  approved = process.env[GEMINI_OAUTH_APPROVAL_FLAG] === "1",
  settingsPath = DEFAULT_SETTINGS_PATH, oauthPath = DEFAULT_OAUTH_PATH,
  fileReader = readFile,
} = {}) {
  if (approved !== true) throw new Error("gemini_oauth_model_not_authorized");
  try {
    const [settings, credential] = await Promise.all([
      fileReader(settingsPath, "utf8"), fileReader(oauthPath, "utf8"),
    ]);
    return checkOAuthData(JSON.parse(settings), JSON.parse(credential), { approved });
  } catch (error) {
    if (String(error?.message || "").startsWith("gemini_oauth_")) throw error;
    throw new Error("gemini_oauth_local_credentials_unavailable");
  }
}
