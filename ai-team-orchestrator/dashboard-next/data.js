/* AI Team Dashboard v2: read-only API adapter, no mocked completion. */
(function (root) {
  "use strict";
  const ACTIVE = new Set(["running", "claimed", "assigned", "primary_running", "review_running", "implementation_running", "validation_running"]);
  const FINISHED = new Set(["completed", "complete", "done", "passed", "accepted", "accept"]);
  const FAILED = new Set(["failed", "error", "rejected", "blocked", "cancelled"]);
  const LANGUAGES = ["zh", "en", "ja", "ko"];
  function safeText(v, fallback = "—") {
    if (typeof v === "string") return v.trim().slice(0, 500) || fallback;
    if (typeof v === "number" && Number.isFinite(v)) return String(v);
    return fallback;
  }
  function percent(v) {
    // Missing progress does not mean 0%. Never infer progress from task status.
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 100) return null;
    return Math.round(v * 10) / 10;
  }
  function safeArray(v, max = 80) {
    return Array.isArray(v) ? v.slice(0, max) : [];
  }
  function safeStatus(v) {
    return typeof v === "string" && /^[a-z_ -]{1,55}$/i.test(v) ? v.toLowerCase().trim() : "unknown";
  }
  function statusKind(s) {
    const v = safeStatus(s);
    if (ACTIVE.has(v)) return "running";
    if (FINISHED.has(v)) return "done";
    if (FAILED.has(v)) return "failed";
    if (v === "queued" || v === "pending" || v === "not_started" || v === "waiting") return "pending";
    return "unknown";
  }
  function normalizeTask(t) {
    const p = t && typeof t.progress === "object" ? t.progress : {};
    return {
      id: safeText(t && (t.task_id || t.id), "未提供 ID"),
      title: safeText(t && t.title, safeText(t && t.task_id, "未命名任务")),
      status: safeStatus(t && t.status),
      worker: safeText(t && (t.agent_id || t.worker_id || t.worker || t.reviewer)),
      phase: safeText(t && (t.phase || t.team_phase)),
      progress: percent(p && p.percent),
      progressLabel: safeText(p && (p.message || p.stage), ""),
      source: safeText(t && t.source, "unknown"),
    };
  }
  function normalizeRun(r) {
    const phases = r && typeof r.phases === "object" && !Array.isArray(r.phases)
      ? Object.entries(r.phases).slice(0, 20).map(([name, v]) => ({
          name: safeText(name), status: safeStatus(v && (v.status || v.state)),
          progress: percent(v && v.percent),
        }))
      : safeArray(r && r.phases, 20).map((v) => ({
          name: safeText(v && (v.name || v.phase)), status: safeStatus(v && (v.status || v.state)),
          progress: percent(v && v.percent),
        }));
    return {
      id: safeText(r && r.run_id),
      name: safeText(r && (r.goal || r.title || r.run_id), "未命名项目"),
      status: safeStatus(r && r.status),
      progress: percent(r && r.overall_percent),
      progressSource: safeText(r && r.percent_source, "Supervisor"),
      blockers: safeArray(r && r.blockers, 12).map(x => safeText(x)),
      phases, tasks: safeArray(r && r.tasks, 120).map(normalizeTask),
    };
  }
  function normalizeWorker(w) {
    const online = w && w.online === true;
    const activeCount = w && Number.isInteger(w.active_count) && w.active_count >= 0 ? w.active_count : null;
    return {
      id: safeText(w && (w.agent_id || w.id)),
      online, busy: online && w.busy === true,
      activeCount,
      tasks: safeArray(w && w.tasks, 15).map(normalizeTask),
      // Do not assert unavailable workers are online merely because a task says running.
    };
  }
  function normalizeMonitor(m) {
    const data = m && typeof m === "object" ? m : {};
    return {
      bridgeAvailable: data.bridge_available === true,
      runs: safeArray(data.runs, 40).map(normalizeRun),
      workers: safeArray(data.workers, 60).map(normalizeWorker),
      taskSampleSize: Number.isInteger(data.task_sample_size) && data.task_sample_size >= 0
        ? data.task_sample_size : null,
    };
  }
  function summary(m) {
    const view = normalizeMonitor(m);
    return {
      bridgeAvailable: view.bridgeAvailable,
      workersOnline: view.workers.filter(w => w.online).length,
      workersTotal: view.workers.length,
      workersBusy: view.workers.filter(w => w.busy).length,
      activeTasksSampled: view.workers.reduce((sum, w) => sum + (w.activeCount || 0), 0),
      knownProjects: view.runs.length,
      blockedProjects: view.runs.filter(r => statusKind(r.status) === "failed").length,
    };
  }
  function taskSummary(t) {
    const task = normalizeTask(t);
    return { ...task, kind: statusKind(task.status) };
  }
  const api = { LANGUAGES, safeText, percent, safeStatus, statusKind,
    normalizeTask, normalizeRun, normalizeWorker, normalizeMonitor, summary, taskSummary };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.DashboardData = api;
})(typeof window === "undefined" ? {} : window);
