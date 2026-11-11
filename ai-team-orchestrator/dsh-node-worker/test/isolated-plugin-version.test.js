/** 离线隔离版本门禁：新版国际请求头修复必须搭配被允许的 DSH 内核。 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  assertInternationalFreeEligible, REQUIRED_PLUGIN_VERSION,
} from "../src/international-free-guard.js";

const NOW = Date.UTC(2026, 9, 18, 0, 0);
const CONFIG = [
  "- id: agent-default-model",
  "  name: '@deepseek-ai/dsh-agent-default-model'",
  "  config:", "    provider: workbuddy-ai", "    model: deepseek-v4.1-flash",
  "- insert:", "    - id: llm-workbuddy", "      name: dsh-workbuddy-connect",
].join("\n");

async function tempEnvironment(pluginVersion, coreVersion, fn) {
  const home = await mkdtemp(join(tmpdir(), "dsh-isolated-version-"));
  const oldHome = process.env.DSH_HOME;
  const oldProfile = process.env.DSH_PROFILE;
  try {
    const dshHome = join(home, ".dsh");
    const plugin = join(dshHome, "profiles/ai-team-intl/node_modules/dsh-workbuddy-connect");
    const core = join(home, ".local/state/ai-team/dsh-intl-runtime/node_modules/@deepseek-ai/dsh");
    await mkdir(plugin, { recursive: true });
    await mkdir(core, { recursive: true });
    await writeFile(join(plugin, "package.json"), JSON.stringify({ version: pluginVersion }));
    await writeFile(join(core, "package.json"), JSON.stringify({ version: coreVersion }));
    await writeFile(join(dshHome, ".workbuddy-ai-catalog.json"), JSON.stringify({
      version: 1, entries: { "synthetic-account": {
        source: "workbuddy-ai:app", fetchedAtMs: NOW - 1000,
        models: [{ id: "deepseek-v4.1-flash", billing: { free: true, credits: "x0.00" },
                   promotions: [{ factor: 0, start: NOW - 600_000,
                                  end: NOW + 3_600_000 }] }],
      } },
    }));
    process.env.DSH_HOME = dshHome;
    process.env.DSH_PROFILE = "ai-team-intl";
    await fn({ home });
  } finally {
    if (oldHome === undefined) delete process.env.DSH_HOME;
    else process.env.DSH_HOME = oldHome;
    if (oldProfile === undefined) delete process.env.DSH_PROFILE;
    else process.env.DSH_PROFILE = oldProfile;
    await rm(home, { recursive: true, force: true });
  }
}

const fakeCmd = async (bin, argv) => ({
  stdout: argv.includes("status")
    ? JSON.stringify({ provider: "workbuddy-ai", domain: "www.workbuddy.ai",
                       status: "signed-in", source: "desktop" })
    : CONFIG,
});

test("0.7.6 与 DSH 0.2.0-rc.2 严格绑定，且全部校验仅使用假命令", async () => {
  assert.equal(REQUIRED_PLUGIN_VERSION, "0.7.6");
  await tempEnvironment("0.7.6", "0.2.0-rc.2", async ({ home }) => {
    let calls = 0;
    const result = await assertInternationalFreeEligible({
      home, now: NOW,
      runCommand: async (...args) => { calls++; return fakeCmd(...args); },
    });
    assert.equal(result.model, "deepseek-v4.1-flash");
    assert.equal(calls, 2);
  });
});

test("旧版 0.7.2 和未经审核的 0.7.7/0.7.8 均拒绝，且不调用任何 CLI", async () => {
  for (const version of ["0.7.2", "0.7.7", "0.7.8"]) {
    await tempEnvironment(version, "0.2.0-rc.2", async ({ home }) => {
      await assert.rejects(assertInternationalFreeEligible({
        home, now: NOW,
        runCommand: async () => { throw new Error("must_not_call_cli"); },
      }), /incorrect_isolated_plugin_version/);
    });
  }
});

test("新版 DSH 内核不自动放行", async () => {
  await tempEnvironment("0.7.6", "0.2.1", async ({ home }) => {
    await assert.rejects(assertInternationalFreeEligible({
      home, now: NOW,
      runCommand: async () => { throw new Error("must_not_call_cli"); },
    }), /incorrect_isolated_core_version/);
  });
});
