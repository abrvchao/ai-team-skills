import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  expectedArtifacts,
  promptArtifacts,
  reportArtifacts,
  requiredArtifacts,
} from "../src/index.js";

async function workspaceWith(files) {
  const root = await mkdtemp(join(tmpdir(), "dsh-artifacts-"));
  for (const file of files) {
    const target = join(root, file);
    await mkdir(join(target, ".."), { recursive: true });
    await writeFile(target, "x", "utf8");
  }
  return root;
}

function collector() {
  const reported = [];
  return {
    reported,
    addArtifact: async (artifact) => {
      reported.push(artifact.path);
      return artifact;
    },
  };
}

test("prompt-named files are extracted from a mixed-language prompt", () => {
  const prompt =
    "在当前工作目录生成 dsh-main-online-proof.txt，写入 DSH main online 和当前时间，并把该文件作为 artifact 返回。";
  assert.deepEqual(promptArtifacts(prompt), ["dsh-main-online-proof.txt"]);
});

test("prompt artifacts dedupe, strip ./ and keep relative paths", () => {
  const prompt = "write ./out/report.json then out/report.json and summary.md";
  assert.deepEqual(promptArtifacts(prompt), ["out/report.json", "summary.md"]);
});

test("prompt artifacts ignore prose that is not a file", () => {
  assert.deepEqual(promptArtifacts("just explain the design, no files"), []);
});

test("required artifacts follow metadata and the known logical task", () => {
  assert.deepEqual(
    requiredArtifacts({ task_id: "t1", prompt: "", metadata: {} }),
    [],
  );
  assert.deepEqual(
    requiredArtifacts({
      task_id: "t2",
      prompt: "",
      metadata: { expected_artifacts: ["out/a.json"] },
    }),
    ["out/a.json"],
  );
  assert.deepEqual(
    requiredArtifacts({
      task_id: "t3",
      prompt: "",
      metadata: { logical_task_id: "CN-DSH-001" },
    }),
    ["docs/research/china-social/zhihu-acquisition.md"],
  );
});

test("expected artifacts merge required and prompt candidates without duplicates", () => {
  const task = {
    task_id: "t4",
    prompt: "produce out/a.json and proof.txt",
    metadata: { expected_artifacts: ["out/a.json"] },
  };
  assert.deepEqual(expectedArtifacts(task), ["out/a.json", "proof.txt"]);
});

test("regression: the post-merge proof filename is discovered", () => {
  const task = {
    task_id: "t5",
    prompt: "create dsh-main-online-proof.txt and return it as artifact",
    metadata: {},
  };
  assert.deepEqual(expectedArtifacts(task), ["dsh-main-online-proof.txt"]);
});

test("a produced prompt-named file is reported as an artifact", async () => {
  const root = await workspaceWith(["dsh-main-online-proof.txt"]);
  try {
    const sink = collector();
    const artifacts = await reportArtifacts({
      task: {
        task_id: "t6",
        workspace: root,
        prompt: "create dsh-main-online-proof.txt and return it as artifact",
        metadata: {},
      },
      addArtifact: sink.addArtifact,
    });
    assert.deepEqual(sink.reported, ["dsh-main-online-proof.txt"]);
    assert.deepEqual(artifacts, [
      { path: "dsh-main-online-proof.txt", kind: "file", label: "dsh-main-online-proof.txt" },
    ]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a prompt-named file that was not produced is skipped, not failed", async () => {
  const root = await workspaceWith([]);
  try {
    const sink = collector();
    const artifacts = await reportArtifacts({
      task: {
        task_id: "t7",
        workspace: root,
        prompt: "create missing.txt",
        metadata: {},
      },
      addArtifact: sink.addArtifact,
    });
    assert.deepEqual(sink.reported, []);
    assert.deepEqual(artifacts, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a missing required artifact fails the task", async () => {
  const root = await workspaceWith([]);
  try {
    const sink = collector();
    await assert.rejects(
      () =>
        reportArtifacts({
          task: {
            task_id: "t8",
            workspace: root,
            prompt: "",
            metadata: { expected_artifacts: ["must-exist.json"] },
          },
          addArtifact: sink.addArtifact,
        }),
      /Expected artifact was not created: must-exist\.json/,
    );
    assert.deepEqual(sink.reported, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("nested prompt paths are reported relative to the workspace", async () => {
  const root = await workspaceWith(["docs/research/note.md"]);
  try {
    const sink = collector();
    await reportArtifacts({
      task: {
        task_id: "t9",
        workspace: root,
        prompt: "write docs/research/note.md",
        metadata: {},
      },
      addArtifact: sink.addArtifact,
    });
    assert.deepEqual(sink.reported, ["docs/research/note.md"]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
