"use strict";

const state = {
  scope: "AI",
  workspaceId: "",
  workspaces: [],
  opportunities: [],
  providers: [],
  selectedTopicId: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    if (url.protocol === "http:" || url.protocol === "https:") return url.href;
  } catch (_) {}
  return null;
}

function number(value, digits = 0) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return parsed.toFixed(digits);
}

function formatDate(value, short = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", short
    ? { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
  ).format(date);
}

async function api(path) {
  const response = await fetch(path, { headers: { "Accept": "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.error?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function statusClass(status) {
  if (status === "healthy" || status === "stored") return "good";
  if (["degraded", "stale", "rate_limited", "auth_required"].includes(status)) return "warn";
  if (["failed", "disabled"].includes(status)) return "bad";
  return "neutral";
}

function clear(node) {
  node.replaceChildren();
}

function showError(message) {
  const node = $("global-error");
  node.textContent = message;
  node.hidden = false;
}

function clearError() {
  $("global-error").hidden = true;
  $("global-error").textContent = "";
}

function renderSummary(payload) {
  const run = payload.run;
  $("metric-opportunities").textContent = String(payload.items.length);
  $("metric-providers").textContent = String(state.providers.length);
  $("metric-last-run").textContent = run ? formatDate(run.generated_at, true) : "—";

  const summary = $("run-summary");
  if (!run) {
    summary.textContent = `Scope “${state.scope}” 暂无持久化 Radar 结果。`;
    return;
  }
  summary.textContent = [
    `Scope “${run.scope}”`,
    `${run.candidate_count} candidates`,
    `${run.scanned_count} deep scans`,
    `seed: ${run.seed_source_mode}`,
    `updated ${formatDate(run.generated_at)}`,
  ].join(" · ");
}

function componentChip(label, value) {
  const chip = el("span", "chip");
  chip.append(el("span", "", label), el("strong", "", number(value)));
  return chip;
}

function renderOpportunities(payload) {
  const list = $("opportunity-list");
  const empty = $("empty-state");
  clear(list);

  state.opportunities = payload.items || [];
  const status = $("opportunity-status");
  status.textContent = state.opportunities.length ? "Ready" : "Empty";
  status.className = `status-pill ${state.opportunities.length ? "good" : "neutral"}`;

  empty.hidden = state.opportunities.length > 0;
  renderSummary(payload);

  state.opportunities.forEach((item) => {
    const card = el("button", "opportunity-card");
    card.type = "button";
    card.addEventListener("click", () => openOpportunity(item.topic_id));

    const top = el("div", "card-top");
    top.append(
      el("div", "rank-number", `#${item.rank}`),
      el("div", "card-title", item.topic),
    );
    const score = el("div", "card-score", number(item.scores?.rank));
    score.append(el("small", "", "rank score"));
    top.append(score);
    card.append(top);

    const chips = el("div", "card-meta");
    chips.append(
      componentChip("Opportunity", item.scores?.opportunity),
      componentChip("Confidence", item.confidence),
      componentChip("Freshness", item.freshness),
      el("span", "chip", `${item.evidence_ids?.length || 0} evidence`),
    );
    if (item.research_pack_available) chips.append(el("span", "chip", "Research Pack"));
    card.append(chips);

    const firstReason = (item.reasons || [])[0];
    if (firstReason) card.append(el("p", "card-reason", firstReason));
    list.append(card);
  });
}

function renderProviders(items) {
  state.providers = items || [];
  $("metric-providers").textContent = String(state.providers.length);
  const list = $("provider-list");
  clear(list);

  if (!state.providers.length) {
    list.append(el("p", "muted", "No persistent collector job state yet."));
    return;
  }

  state.providers.forEach((provider) => {
    const item = el("div", "provider-item");
    const top = el("div", "provider-top");
    top.append(
      el("div", "provider-name", provider.provider),
      el("span", `status-pill ${statusClass(provider.status)}`, provider.status),
    );
    item.append(top);

    const meta = el("div", "provider-meta");
    meta.append(
      el("span", "", `Events: ${provider.event_count ?? 0}`),
      el("span", "", `Next: ${formatDate(provider.next_due_at, true)}`),
      el("span", "", `Last: ${formatDate(provider.last_attempted_at, true)}`),
      el("span", "", `Success: ${formatDate(provider.last_successful_at, true)}`),
    );
    item.append(meta);

    if (provider.warnings?.length) {
      item.append(el("p", "muted", provider.warnings[0]));
    }
    list.append(item);
  });
}

function renderComponents(components) {
  const grid = $("component-grid");
  clear(grid);
  const labels = {
    demand: "Demand",
    momentum: "Momentum",
    supply_gap: "Supply gap",
    authority_fit: "Authority fit",
    business_fit: "Business fit",
    freshness: "Freshness",
    confidence: "Confidence",
  };
  Object.entries(labels).forEach(([key, label]) => {
    const value = Math.max(0, Math.min(100, Number(components?.[key] || 0)));
    const row = el("div", "component-row");
    row.append(el("span", "component-name", label));
    const track = el("div", "component-track");
    const fill = el("div", "component-fill");
    fill.style.width = `${value}%`;
    track.append(fill);
    row.append(track, el("span", "component-value", number(value)));
    grid.append(row);
  });
}

function renderSources(items) {
  const root = $("source-summary");
  clear(root);
  if (!items?.length) {
    root.append(el("p", "muted", "No source summary available."));
    return;
  }
  items.forEach((source) => {
    const row = el("div", "source-item");
    const left = el("span", "", `${source.provider} · ${source.source}`);
    const right = el("strong", "", String(source.evidence_count));
    row.append(left, right);
    root.append(row);
  });
}

function renderHistory(items) {
  const root = $("history-chart");
  clear(root);
  if (!items?.length) {
    root.append(el("p", "muted", "No history yet."));
    return;
  }

  const ordered = [...items].reverse();
  const values = ordered.map((row) => Number(row.scores?.rank || 0));
  const width = 420;
  const height = 130;
  const pad = 12;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 100);
  const span = Math.max(1, max - min);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Opportunity rank score history");

  const points = values.map((value, index) => {
    const x = ordered.length === 1
      ? width / 2
      : pad + index * ((width - pad * 2) / (ordered.length - 1));
    const y = height - pad - ((value - min) / span) * (height - pad * 2);
    return [x, y];
  });

  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", points.map(([x, y]) => `${x},${y}`).join(" "));
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "currentColor");
  line.setAttribute("stroke-width", "3");
  line.setAttribute("stroke-linecap", "round");
  line.setAttribute("stroke-linejoin", "round");
  line.style.color = "var(--accent)";
  svg.append(line);

  points.forEach(([x, y]) => {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", String(x));
    dot.setAttribute("cy", String(y));
    dot.setAttribute("r", "4");
    dot.setAttribute("fill", "currentColor");
    dot.style.color = "var(--accent-2)";
    svg.append(dot);
  });
  root.append(svg);

  const labels = el("div", "history-labels");
  labels.append(
    el("span", "", formatDate(ordered[0]?.created_at, true)),
    el("span", "", `${ordered.length} snapshots`),
    el("span", "", formatDate(ordered.at(-1)?.created_at, true)),
  );
  root.append(labels);
}

function renderEvidence(items) {
  const root = $("evidence-list");
  clear(root);
  if (!items.length) {
    root.append(el("p", "muted", "Evidence details are not available for this snapshot."));
    return;
  }

  items.forEach((item) => {
    const card = el("article", "evidence-item");
    const heading = el("div", "evidence-title");
    heading.append(
      el("strong", "", item.title || item.evidence_id),
      el("span", "chip", item.provider),
    );
    card.append(heading);

    if (item.text && item.text !== item.title) {
      const text = item.text.length > 360 ? `${item.text.slice(0, 357)}…` : item.text;
      card.append(el("p", "", text));
    }

    const meta = el("div", "evidence-meta");
    meta.append(
      el("span", "chip", item.acquisition_method || "unknown"),
      el("span", "chip", formatDate(item.published_at || item.retrieved_at, true)),
    );
    const metricKeys = Object.keys(item.metrics || {}).slice(0, 3);
    metricKeys.forEach((key) => meta.append(
      el("span", "chip", `${key}: ${number(item.metrics[key], 1)}`)
    ));
    card.append(meta);

    const url = safeHttpUrl(item.url);
    if (url) {
      const link = el("a", "", "Open source ↗");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    }
    root.append(card);
  });
}

function renderResearchPack(pack) {
  const section = $("research-section");
  const root = $("research-pack");
  clear(root);
  if (!pack) {
    section.hidden = true;
    return;
  }
  section.hidden = false;

  const citations = pack.citations?.length || 0;
  const questions = pack.questions?.length || 0;
  const pain = pack.pain_signals?.length || 0;
  const gaps = pack.evidence_gaps?.length || 0;

  [
    ["Citations", citations],
    ["Questions", questions],
    ["Pain / requests", pain],
    ["Evidence gaps", gaps],
  ].forEach(([label, value]) => {
    const row = el("div", "research-stat");
    row.append(el("strong", "", String(value)), document.createTextNode(` ${label}`));
    root.append(row);
  });
}

function workspaceQuery() {
  return state.workspaceId
    ? `&workspace_id=${encodeURIComponent(state.workspaceId)}`
    : "";
}

async function openOpportunity(topicId) {
  state.selectedTopicId = topicId;
  const panel = $("detail-panel");
  panel.hidden = false;
  $("detail-title").textContent = "Loading…";
  $("detail-subtitle").textContent = "";
  panel.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const encoded = encodeURIComponent(topicId);
    const scope = encodeURIComponent(state.scope);
    const workspace = workspaceQuery();
    const [detail, history] = await Promise.all([
      api(`/v1/opportunities/${encoded}?scope=${scope}${workspace}`),
      api(`/v1/opportunities/${encoded}/history?scope=${scope}&limit=90${workspace}`),
    ]);

    if (state.selectedTopicId !== topicId) return;

    $("detail-rank").textContent = `Rank #${detail.rank} · ${detail.scope}`;
    $("detail-title").textContent = detail.topic;
    $("detail-subtitle").textContent =
      `Opportunity ${number(detail.scores?.opportunity)} · Rank score ${number(detail.scores?.rank)} · Confidence ${number(detail.confidence)}`;

    renderComponents(detail.components);
    renderSources(detail.source_summary);
    renderHistory(history.items || []);

    const reasons = $("reason-list");
    clear(reasons);
    (detail.reasons || []).forEach((reason) => reasons.append(el("li", "", reason)));
    if (!reasons.children.length) reasons.append(el("li", "muted", "No explanation recorded."));

    const ids = (detail.evidence_ids || []).slice(0, 8);
    $("evidence-count").textContent =
      `showing ${ids.length} of ${detail.evidence_ids?.length || 0}`;
    const evidenceRows = await Promise.all(
      ids.map((id) => api(`/v1/evidence/${encodeURIComponent(id)}`).catch(() => null))
    );
    renderEvidence(evidenceRows.filter(Boolean));

    if (detail.research_pack_available) {
      const pack = await api(
        `/v1/opportunities/${encoded}/research-pack?scope=${scope}${workspace}`
      ).catch(() => null);
      renderResearchPack(pack);
    } else {
      renderResearchPack(null);
    }
  } catch (error) {
    showError(`Opportunity detail failed: ${error.message}`);
    $("detail-title").textContent = "Unable to load opportunity";
  }
}

async function loadWorkspaces() {
  const payload = await api("/v1/workspaces").catch(() => ({ items: [] }));
  state.workspaces = payload.items || [];

  const select = $("workspace-select");
  const current = state.workspaceId;
  while (select.options.length > 1) select.remove(1);

  state.workspaces.forEach((workspace) => {
    const option = document.createElement("option");
    option.value = workspace.workspace_id;
    option.textContent = workspace.name;
    select.append(option);
  });

  if (current && state.workspaces.some((item) => item.workspace_id === current)) {
    select.value = current;
  } else if (current) {
    state.workspaceId = "";
    select.value = "";
  }
}

async function loadDashboard() {
  clearError();
  $("opportunity-status").textContent = "Loading";
  $("opportunity-status").className = "status-pill neutral";
  try {
    const scope = encodeURIComponent(state.scope);
    const opportunityPath = state.workspaceId
      ? `/v1/workspaces/${encodeURIComponent(state.workspaceId)}/opportunities?limit=20`
      : `/v1/opportunities?scope=${scope}&limit=20`;

    const [opportunities, providers] = await Promise.all([
      api(opportunityPath),
      api("/v1/providers"),
    ]);
    renderProviders(providers.items || []);
    renderOpportunities(opportunities);
  } catch (error) {
    showError(`Radar load failed: ${error.message}`);
    renderProviders([]);
    renderOpportunities({ run: null, items: [] });
    $("opportunity-status").textContent = "Error";
    $("opportunity-status").className = "status-pill bad";
  }
}

$("workspace-select").addEventListener("change", () => {
  state.workspaceId = $("workspace-select").value;
  const workspace = state.workspaces.find(
    (item) => item.workspace_id === state.workspaceId
  );
  if (workspace) {
    state.scope = workspace.scope || "AI";
    $("scope-input").value = state.scope;
  }
  $("detail-panel").hidden = true;
  state.selectedTopicId = null;
  (async () => {
  await loadWorkspaces();
  await loadDashboard();
})();
});

$("scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = $("scope-input").value.trim();
  state.scope = value || "AI";
  $("detail-panel").hidden = true;
  state.selectedTopicId = null;
  loadDashboard();
});

$("close-detail").addEventListener("click", () => {
  $("detail-panel").hidden = true;
  state.selectedTopicId = null;
});

loadDashboard();
