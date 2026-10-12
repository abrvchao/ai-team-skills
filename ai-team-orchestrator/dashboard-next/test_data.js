// Tests are synthetic and offline; they must never contact Bridge or platform APIs.
const test = require("node:test");
const assert = require("node:assert/strict");
const d = require("./data.js");

test("unknown, booleans and out-of-range percentages stay unknown", () => {
  for (const n of [undefined, null, "", "95", true, false, -1, 101, Infinity, NaN]) {
    assert.equal(d.percent(n), null);
  }
  assert.equal(d.percent(0), 0);
  assert.equal(d.percent(45.67), 45.7);
});
test("blocked phase does not manufacture 100% completion", () => {
  const run = d.normalizeRun({
    run_id: "r1", status: "blocked", overall_percent: 80,
    phases: { validation: { state: "blocked", percent: 0 } },
    tasks: [{ task_id: "t1", status: "completed" }],
  });
  assert.equal(run.progress, 80);
  assert.equal(run.tasks[0].progress, null);
  assert.equal(run.phases[0].progress, 0);
  assert.equal(d.statusKind(run.status), "failed");
});
test("offline workers never show as busy regardless of task state", () => {
  const worker = d.normalizeWorker({
    agent_id: "dsh", online: false, busy: true,
    tasks: [{ task_id: "t1", status: "running" }],
  });
  assert.equal(worker.online, false);
  assert.equal(worker.busy, false);
  assert.equal(worker.tasks[0].progress, null);
});
test("dashboard never assumes bridge live because /api/monitor returned JSON", () => {
  const s = d.summary({
    bridge_available: false, runs: [{ status: "blocked" }],
    workers: [{ agent_id: "qwen", online: true, busy: true, active_count: 1 }],
  });
  assert.equal(s.bridgeAvailable, false);
  assert.equal(s.blockedProjects, 1);
  assert.equal(s.workersBusy, 1);
});
test("normalizeTask never expands unknown source content", () => {
  const t = d.normalizeTask({
    task_id: "t1", title: "<img src=x onerror=alert(1)>",
    progress: { percent: "100", message: "<script>bad</script>" },
  });
  assert.equal(t.progress, null);
  assert.equal(t.title, "<img src=x onerror=alert(1)>");
  assert.equal(t.progressLabel, "<script>bad</script>");
  // Rendering uses textContent, never innerHTML, so these remain literal text.
});
test("samples and run list are bounded for large histories", () => {
  const m = d.normalizeMonitor({
    bridge_available: true,
    runs: Array.from({ length: 100 }, (_, i) => ({ run_id: String(i) })),
    workers: Array.from({ length: 100 }, (_, i) => ({ agent_id: String(i) })),
    task_sample_size: 100,
  });
  assert.equal(m.runs.length, 40);
  assert.equal(m.workers.length, 60);
  assert.equal(m.taskSampleSize, 100);
});
test("data adapters retain phases as objects or arrays", () => {
  const byObject = d.normalizeRun({ phases: { repair: { state: "complete", percent: 100 } } });
  const byArray = d.normalizeRun({ phases: [{ name: "review", state: "running", percent: 25 }] });
  assert.equal(byObject.phases[0].name, "repair");
  assert.equal(byArray.phases[0].name, "review");
  assert.equal(byArray.phases[0].progress, 25);
});
test("status mapping is explicit and no unsupported optimism", () => {
  assert.equal(d.statusKind("accepted"), "done");
  assert.equal(d.statusKind("running"), "running");
  assert.equal(d.statusKind("blocked"), "failed");
  assert.equal(d.statusKind("queued"), "pending");
  assert.equal(d.statusKind("awaiting_review"), "unknown");
});
