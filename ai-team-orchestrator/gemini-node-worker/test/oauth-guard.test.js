// 单测完全离线，不调用 Google 模型或任何收费 API。
import test from "node:test";
import assert from "node:assert/strict";
import {
  AUTH_TYPE, assertGeminiOAuthReady, checkOAuthData,
  oauthOnlyEnvironment,
} from "../src/oauth-guard.js";
import { startGeminiWorker } from "../src/readiness.js";
import { runGeminiPrompt } from "../src/index.js";

const account = { refresh_token: "synthetic_refresh_token_123456" };
const settings = { security: { auth: { selectedType: AUTH_TYPE } } };

test("只允许显式批准的 OAuth，不允许 API Key 或缺登录", () => {
  assert.throws(() => checkOAuthData(settings, account), /not_authorized/);
  assert.deepEqual(checkOAuthData(settings, account, { approved: true }),
                   { auth: "oauth-personal", refreshCredentialPresent: true });
  for (const bad of ["api-key", "vertex-ai", "oauth-legacy", undefined]) {
    assert.throws(() => checkOAuthData(
      { security: { auth: { selectedType: bad } } }, account, { approved: true }),
      /oauth_not_selected/);
  }
  assert.throws(() => checkOAuthData(settings, {}, { approved: true }), /login_required/);
});

test("传给 CLI 的环境自动剔除 API 计费相关变量，原值保持不变", () => {
  const source = {
    HOME: "/tmp/test", PATH: "/usr/bin", GEMINI_API_KEY: "synthetic-secret",
    GOOGLE_API_KEY: "synthetic-secret", GOOGLE_GENAI_USE_VERTEXAI: "true",
    GOOGLE_APPLICATION_CREDENTIALS: "/tmp/service-account", GOOGLE_CLOUD_PROJECT: "billed-project",
    GEMINI_CLI_API_KEY: "synthetic-secret", GOOGLE_CLOUD_LOCATION: "us-central1",
  };
  const result = oauthOnlyEnvironment(source);
  assert.equal(result.HOME, "/tmp/test");
  for (const key of Object.keys(source).filter((v) => v !== "HOME" && v !== "PATH")) {
    assert.equal(Object.hasOwn(result, key), false, key);
  }
  assert.equal(source.GEMINI_API_KEY, "synthetic-secret");
});

test("仅检查本地合成 OAuth 设置，绝不发网络请求", async () => {
  const result = await assertGeminiOAuthReady({
    approved: true,
    fileReader: async (name) => name.endsWith("settings.json")
      ? JSON.stringify(settings) : JSON.stringify(account),
  });
  assert.equal(result.auth, AUTH_TYPE);
  await assert.rejects(assertGeminiOAuthReady({ approved: false }), /not_authorized/);
  await assert.rejects(assertGeminiOAuthReady({ approved: true,
    fileReader: async () => { throw new Error("private credential details"); },
  }), /local_credentials_unavailable/);
});

test("OAuth 未授权时直接拒绝真实 Gemini 进程，连假执行器也不启动", async () => {
  await assert.rejects(runGeminiPrompt({
    task: { workspace: "/tmp", prompt: "hi" }, prompt: "hi", geminiBin: "/bin/false",
    authGate: async () => { throw new Error("gemini_oauth_model_not_authorized"); },
  }), /not_authorized/);
});

test("默认只预检一次；失败不启动 Worker，不定时再次调用", async () => {
  let attempts = 0;
  let registered = 0;
  let readinessFailures = 0;
  const r = await startGeminiWorker({
    client: { reportReadiness: async (msg) => { if (!msg.modelAvailable) readinessFailures++; } },
    bootstrapToken: "synthetic-bootstrap",
    preflight: async () => { attempts++; throw new Error("synthetic_auth_rejected"); },
    workerFactory: () => ({ start: async () => { registered++; } }),
    sleepImpl: async () => { throw new Error("unexpected_retry_sleep"); },
  });
  assert.equal(r.started, false);
  assert.equal(attempts, 1);
  assert.equal(registered, 0);
  assert.equal(readinessFailures, 1);
});
