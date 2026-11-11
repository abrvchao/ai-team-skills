"use strict";

const state = {
  scope: "AI",
  language: "en",
  country: "US",
  workspaceId: "",
  workspaces: [],
  opportunities: [],
  providers: [],
  selectedTopicId: null,
  selectedOpportunity: null,
  lastOpportunityPayload: null,
  translationCache: new Map(),
};

const I18N = window.RadarI18n;

function t(key) {
  return I18N.t(state.language, key);
}

function tf(key, values = {}) {
  return I18N.tf(state.language, key, values);
}

function uiText() {
  return I18N.get(state.language);
}

function selectedLanguageConfig() {
  const select = $("language-select");
  const option = select.options[select.selectedIndex];
  return {
    language: I18N.normalize(select.value || "en"),
    country: option?.dataset?.country || "US",
  };
}

function applyInterfaceLanguage() {
  state.language = I18N.normalize(state.language);
  document.documentElement.lang = I18N.locale(state.language);
  I18N.applyStatic(document, state.language);
  // 中文说明：帮助面板独立于扫描数据；不会把开发清单进度伪装成实时采集成功率。
  const guide = state.language === "zh" ? {
    "plain-guide-eyebrow": "先看这里 · 使用说明",
    "plain-guide-title": "Radar 现在做到哪一步？",
    "plain-guide-stage": "R3 · 真实使用验证中",
    "plain-guide-explain": "Radar 精简版功能已具备，但“准确、稳定、有用”还需要真实用户验证；下方的机会数量是扫描结果，不是开发完成率。",
    "plain-guide-progress-label": "开发检查项：11 / 18 已完成（61%）",
    "plain-guide-progress-date": "记录日期：2026-10-13 · 阶段性清单，不代表产品质量或商业成熟度",
    "plain-guide-ready-title": "已经可以做什么",
    "plain-guide-ready": "选主题 → 运行扫描 → 查看机会卡与证据 → 交接给 AI Team。",
    "plain-guide-pending-title": "还没完成什么",
    "plain-guide-pending": "真实用户可行动性、授权 GSC 数据、实验信号对照等验收尚未全部通过。",
    "plain-guide-next-title": "你现在怎么看",
    "plain-guide-next": "先看下方“优先机会”，点击感兴趣的卡片，再核对证据。来源状态只是采集健康度。"
  } : {
    "plain-guide-eyebrow": "START HERE · Quick guide",
    "plain-guide-title": "Where is Radar today?",
    "plain-guide-stage": "R3 · Real-user validation",
    "plain-guide-explain": "Radar Lite works, but accuracy, stability, and usefulness still need real-user validation. Opportunity counts below are scan results, not development completion.",
    "plain-guide-progress-label": "Delivery checklist: 11 / 18 complete (61%)",
    "plain-guide-progress-date": "Snapshot: 2026-10-13 · This is milestone tracking, not a measure of product quality.",
    "plain-guide-ready-title": "What works now",
    "plain-guide-ready": "Choose a topic → run a scan → inspect opportunities and evidence → hand off to AI Team.",
    "plain-guide-pending-title": "What's not yet verified",
    "plain-guide-pending": "Real-user actionability, authorized GSC data, and experimental-signal comparisons remain open.",
    "plain-guide-next-title": "What to look at first",
    "plain-guide-next": "Start with Top Opportunities, open a card, then check its evidence. Provider status is only collection health."
  };
  for (const [id, value] of Object.entries(guide)) { const node = $(id); if (node) node.textContent = value; }
  if (!$("run-lite-scan").disabled) $("scan-status").textContent = t("ready");
  if (state.selectedOpportunity) $("handoff-status").textContent = t("handoffReady");
}

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function setContentText(node, text) {
  const source = String(text ?? "").trim();
  node.textContent = source;
  if (source) {
    node.dataset.radarSourceText = source;
  } else {
    delete node.dataset.radarSourceText;
  }
  return node;
}

function contentEl(tag, className, text) {
  return setContentText(el(tag, className), text);
}

function readableEvidenceText(value) {
  // 中文说明：仅清理证据卡片的展示文本，原始证据、引用和评分数据保持不变。
  const decoder = document.createElement("textarea");
  decoder.innerHTML = String(value ?? "");
  return decoder.value
    .replace(/!\[([^\]]*)\]\(https?:\/\/[^\s)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .replace(/(?:https?:\/\/[^\s]+){2,}/g, "[link]")
    .replace(/\s+/g, " ")
    .trim();
}

async function translateContent(root = document) {
  const language = state.language;
  const nodes = Array.from(root.querySelectorAll("[data-radar-source-text]"));
  if (!nodes.length) return;

  const unique = Array.from(new Set(
    nodes.map((node) => node.dataset.radarSourceText || "").filter(Boolean)
  ));
  const missing = unique.filter(
    (text) => !state.translationCache.has(language + "\u0000" + text)
  );

  const failed = new Set();
  // 中文说明：模型翻译采用小批次，每个失败批次单独回退，不影响已有译文。
  async function requestBatch(batch) {
    try {
      const payload = await postApi("/v1/lite/translate", {
        target_language: language,
        texts: batch,
      });
      if (!Array.isArray(payload.translations) || payload.translations.length !== batch.length) {
        throw new Error("Invalid translation response");
      }
      (payload.translations || []).forEach((translated, offset) => {
        state.translationCache.set(
          language + "\u0000" + batch[offset],
          String(translated || batch[offset])
        );
      });
    } catch (error) {
      if (batch.length > 1) {
        const midpoint = Math.ceil(batch.length / 2);
        await requestBatch(batch.slice(0, midpoint));
        await requestBatch(batch.slice(midpoint));
      } else {
        failed.add(batch[0]);
      }
    }
  }

  for (let index = 0; index < missing.length; index += 6) {
    const batch = missing.slice(index, index + 6);
    await requestBatch(batch);
  }

  if (state.language !== language) return;
  nodes.forEach((node) => {
    if (!node.isConnected) return;
    const source = node.dataset.radarSourceText || "";
    const translated = state.translationCache.get(language + "\u0000" + source);
    if (translated && translated !== source) {
      showTranslationNote(node, translated, false);
    } else if (failed.has(source)) {
      showTranslationNote(node, language === "zh" ? "中文翻译暂不可用，已保留原文" : "Translation temporarily unavailable; original preserved", true);
    }
  });
}

function showTranslationNote(node, text, failed) {
  // 中文说明：译文另起一行，不覆盖原始数据；重复请求只更新本节点自己的译文。
  const anchor = node.closest(".evidence-title, .card-top") || node;
  let note = anchor.nextElementSibling;
  if (!note || !note.classList.contains("radar-translation")) {
    note = el("div", "radar-translation");
    anchor.insertAdjacentElement("afterend", note);
  }
  note.classList.toggle("translation-error", failed);
  note.textContent = failed ? text : `${state.language === "zh" ? "中文翻译" : "Translation"} · ${text}`;
  note.lang = failed ? "" : state.language;
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
  return new Intl.DateTimeFormat(I18N.locale(state.language), short
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

async function postApi(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body || {}),
  });
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
    summary.textContent = tf("noSavedResults", { scope: state.scope });
    return;
  }
  summary.textContent = [
    `${t("scopeLabel")} “${run.scope}”`,
    `${run.candidate_count} ${t("candidates")}`,
    `${run.scanned_count} ${t("deepScans")}`,
    `${t("seed")}: ${run.seed_source_mode}`,
    `${t("updated")} ${formatDate(run.generated_at)}`,
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
  state.lastOpportunityPayload = payload;
  const status = $("opportunity-status");
  status.textContent = state.opportunities.length ? t("statusReady") : t("statusEmpty");
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
      contentEl("div", "card-title", item.topic),
    );
    const score = el("div", "card-score", number(item.scores?.rank));
    score.append(el("small", "", t("rankScore")));
    top.append(score);
    card.append(top);

    const chips = el("div", "card-meta");
    chips.append(
      componentChip(t("opportunity"), item.scores?.opportunity),
      componentChip(t("confidence"), item.confidence),
      componentChip(t("freshness"), item.freshness),
      el("span", "chip", tf("evidenceCount", { count: item.evidence_ids?.length || 0 })),
    );
    if (item.research_pack_available) chips.append(el("span", "chip", t("researchPack")));
    card.append(chips);

    const firstReason = (item.reasons || [])[0];
    if (firstReason) card.append(contentEl("p", "card-reason", firstReason));
    list.append(card);
  });
  translateContent(list);
}

function providerStatusLabel(status) {
  const keys = {
    healthy: "statusHealthy",
    stored: "statusStored",
    degraded: "statusDegraded",
    stale: "statusStale",
    rate_limited: "statusRateLimited",
    auth_required: "statusAuthRequired",
    failed: "statusFailed",
    disabled: "statusDisabled",
  };
  return t(keys[status] || status);
}

function renderProviders(items) {
  state.providers = items || [];
  $("metric-providers").textContent = String(state.providers.length);
  const list = $("provider-list");
  clear(list);

  if (!state.providers.length) {
    list.append(el("p", "muted", t("providerNoState")));
    return;
  }

  state.providers.forEach((provider) => {
    const item = el("div", "provider-item");
    const top = el("div", "provider-top");
    top.append(
      el("div", "provider-name", provider.provider),
      el("span", `status-pill ${statusClass(provider.status)}`, providerStatusLabel(provider.status)),
    );
    item.append(top);

    const meta = el("div", "provider-meta");
    meta.append(
      el("span", "", `${t("events")}: ${provider.event_count ?? 0}`),
      el("span", "", `${t("next")}: ${formatDate(provider.next_due_at, true)}`),
      el("span", "", `${t("last")}: ${formatDate(provider.last_attempted_at, true)}`),
      el("span", "", `${t("success")}: ${formatDate(provider.last_successful_at, true)}`),
    );
    item.append(meta);

    if (provider.warnings?.length) {
      item.append(contentEl("p", "muted", provider.warnings[0]));
    }
    list.append(item);
  });
  translateContent(list);
}

function renderComponents(components) {
  const grid = $("component-grid");
  clear(grid);
  const labels = {
    demand: t("componentDemand"),
    momentum: t("componentMomentum"),
    supply_gap: t("componentSupplyGap"),
    authority_fit: t("componentAuthorityFit"),
    business_fit: t("componentBusinessFit"),
    freshness: t("freshness"),
    confidence: t("confidence"),
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
    root.append(el("p", "muted", t("noSourceSummary")));
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
    root.append(el("p", "muted", t("noHistory")));
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
  svg.setAttribute("aria-label", t("historyAria"));

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
    el("span", "", `${ordered.length} ${t("snapshots")}`),
    el("span", "", formatDate(ordered.at(-1)?.created_at, true)),
  );
  root.append(labels);
}

function renderEvidence(items) {
  const root = $("evidence-list");
  clear(root);
  if (!items.length) {
    root.append(el("p", "muted", t("noEvidenceDetails")));
    return;
  }

  items.forEach((item) => {
    const card = el("article", "evidence-item");
    const heading = el("div", "evidence-title");
    heading.append(
      contentEl("strong", "", readableEvidenceText(item.title || item.evidence_id)),
      el("span", "chip", item.provider),
    );
    card.append(heading);

    if (item.text && item.text !== item.title) {
      const cleaned = readableEvidenceText(item.text);
      const text = cleaned.length > 360 ? `${cleaned.slice(0, 357)}…` : cleaned;
      card.append(contentEl("p", "", text));
    }

    const meta = el("div", "evidence-meta");
    meta.append(
      el("span", "chip", item.acquisition_method || t("unknown")),
      el("span", "chip", formatDate(item.published_at || item.retrieved_at, true)),
    );
    const metricKeys = Object.keys(item.metrics || {}).slice(0, 3);
    metricKeys.forEach((key) => meta.append(
      el("span", "chip", `${key}: ${number(item.metrics[key], 1)}`)
    ));
    card.append(meta);

    const url = safeHttpUrl(item.url);
    if (url) {
      const link = el("a", "", t("openSource"));
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    }
    root.append(card);
  });
  translateContent(root);
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
  const questions = pack.key_questions?.length || 0;
  const pain = pack.pain_signals?.length || 0;
  const gaps = pack.coverage?.missing_dimensions?.length || 0;

  [
    [t("citations"), citations],
    [t("questions"), questions],
    [t("painRequests"), pain],
    [t("evidenceGaps"), gaps],
  ].forEach(([label, value]) => {
    const row = el("div", "research-stat");
    row.append(el("strong", "", String(value)), document.createTextNode(` ${label}`));
    root.append(row);
  });

  const action = pack.action_card;
  if (action) {
    root.append(el("h4", "", t("actionCard")));
    [
      [t("firstStep"), action.first_step],
      [t("minimumOutput"), action.minimum_viable_output],
      [t("starterRisk"), action.starter_risk],
      [t("stopRule"), action.stop_rule],
    ].forEach(([label, value]) => {
      if (!value) return;
      const row = el("div", "research-stat");
      row.append(el("strong", "", label + ": "), contentEl("span", "", String(value)));
      root.append(row);
    });
  }
  translateContent(root);
}

function workspaceQuery() {
  return state.workspaceId
    ? `&workspace_id=${encodeURIComponent(state.workspaceId)}`
    : "";
}

async function openOpportunity(topicId) {
  state.selectedTopicId = topicId;
  state.selectedOpportunity = null;
  const panel = $("detail-panel");
  panel.hidden = false;
  $("detail-title").textContent = t("loadingOpportunity");
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

    $("detail-rank").textContent = tf("rankLine", { rank: detail.rank, scope: detail.scope });
    setContentText($("detail-title"), detail.topic);
    $("detail-subtitle").textContent = tf("detailSubtitle", {
      opportunity: number(detail.scores?.opportunity),
      rank: number(detail.scores?.rank),
      confidence: number(detail.confidence),
    });
    state.selectedOpportunity = detail;
    $("handoff-status").textContent = t("handoffReady");

    renderComponents(detail.components);
    renderSources(detail.source_summary);
    renderHistory(history.items || []);

    const reasons = $("reason-list");
    clear(reasons);
    (detail.reasons || []).forEach((reason) => {
      // 中文说明：原始判断依据保留在列表中，译文由翻译层另起一行展示。
      const item = el("li", "");
      item.append(contentEl("div", "reason-original", reason));
      reasons.append(item);
    });
    if (!reasons.children.length) reasons.append(el("li", "muted", t("noExplanation")));
    translateContent(reasons);

    const ids = (detail.evidence_ids || []).slice(0, 8);
    $("evidence-count").textContent = tf("showingEvidence", {
      shown: ids.length,
      total: detail.evidence_ids?.length || 0,
    });
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
    translateContent(panel);
  } catch (error) {
    showError(tf("detailLoadFailed", { message: error.message }));
    $("detail-title").textContent = t("unableLoad");
  }
}

async function loadWorkspaces() {
  let payload;
  try { payload = await api("/v1/workspaces"); }
  catch (error) { showError(tf("radarLoadFailed", { message: error.message })); return; }
  state.workspaces = payload.items || [];

  const select = $("workspace-select");
  const current = state.workspaceId;
  while (select.options.length > 1) select.remove(1);

  state.workspaces.forEach((workspace) => {
    const option = document.createElement("option");
    option.value = workspace.workspace_id;
    option.textContent = workspace.name + " · " + workspace.scope;
    select.append(option);
  });

  if (current && state.workspaces.some((item) => item.workspace_id === current)) {
    select.value = current;
  } else if (current) {
    state.workspaceId = "";
    select.value = "";
    try { window.localStorage.removeItem("radarWorkspaceId"); } catch (_) {}
  }
  $("workspace-edit").disabled = !state.workspaceId;
  // 中文说明：选择工作区后仅通过编辑弹窗调整关注主题。
  $("scope-input").readOnly = Boolean(state.workspaceId);
  $("scope-input").title = state.workspaceId ? t("editWorkspace") : "";
}

async function loadDashboard() {
  clearError();
  $("opportunity-status").textContent = t("loading");
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
    showError(tf("radarLoadFailed", { message: error.message }));
    renderProviders([]);
    renderOpportunities({ run: null, items: [] });
    $("opportunity-status").textContent = t("statusError");
    $("opportunity-status").className = "status-pill bad";
  }
}

$("workspace-create").addEventListener("click", () => openWorkspaceEditor(false));
$("workspace-edit").addEventListener("click", () => openWorkspaceEditor(true));
$("workspace-cancel").addEventListener("click", () => $("workspace-dialog").close());

function openWorkspaceEditor(editing) {
  const existing = editing ? state.workspaces.find((item) => item.workspace_id === state.workspaceId) : null;
  if (editing && !existing) return;
  $("workspace-dialog-title").textContent = t(existing ? "editWorkspace" : "newWorkspace");
  $("workspace-name").value = existing?.name || "";
  $("workspace-scope").value = existing?.scope || $("scope-input").value.trim() || "AI";
  const languageSelect = $("workspace-language");
  languageSelect.querySelectorAll("[data-temporary-language]").forEach((option) => option.remove());
  const language = existing?.language || state.language;
  if (!Array.from(languageSelect.options).some((option) => option.value === language)) {
    const option = new Option(language.toUpperCase(), language);
    option.dataset.temporaryLanguage = "true";
    languageSelect.add(option);
  }
  languageSelect.value = language;
  $("workspace-editor-error").hidden = true;
  $("workspace-dialog").dataset.editing = existing?.workspace_id || "";
  $("workspace-dialog").showModal();
}

$("workspace-editor").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("workspace-editor").querySelector('[type="submit"]');
  const error = $("workspace-editor-error");
  const editing = $("workspace-dialog").dataset.editing;
  const language = $("workspace-language").value;
  const country = Array.from($("language-select").options).find((option) => option.value === language)?.dataset.country || "US";
  submit.disabled = true;
  error.hidden = true;
  try {
    const workspace = await postApi(editing ? "/v1/lite/workspaces/update" : "/v1/lite/workspaces/create", {
      ...(editing ? { workspace_id: editing } : {}),
      name: $("workspace-name").value.trim(),
      scope: $("workspace-scope").value.trim(),
      language,
      country,
    });
    state.workspaceId = workspace.workspace_id;
    $("workspace-dialog").close();
    await loadWorkspaces();
    $("workspace-select").dispatchEvent(new Event("change"));
  } catch (err) {
    const message = String(err.message || "");
    error.textContent = /already exists/i.test(message)
      ? (state.language === "zh" ? "同名且同主题的工作区已存在。" : "A workspace with this name and topic already exists.")
      : /language is required/i.test(message)
      ? (state.language === "zh" ? "请选择工作区语言。" : "Choose a workspace language.")
      : (state.language === "zh" ? "保存工作区失败，请核对信息后重试。" : "Could not save the workspace. Check the details and try again.");
    error.lang = state.language;
    error.hidden = false;
  } finally {
    submit.disabled = false;
  }
});

$("workspace-select").addEventListener("change", () => {
  state.workspaceId = $("workspace-select").value;
  // 中文说明：仅保存工作区标识；启动时必须经 API 校验，不能信任旧缓存。
  try {
    if (state.workspaceId) window.localStorage.setItem("radarWorkspaceId", state.workspaceId);
    else window.localStorage.removeItem("radarWorkspaceId");
  } catch (_) {}
  $("workspace-edit").disabled = !state.workspaceId;
  $("scope-input").readOnly = Boolean(state.workspaceId);
  $("scope-input").title = state.workspaceId ? t("editWorkspace") : "";
  const workspace = state.workspaces.find(
    (item) => item.workspace_id === state.workspaceId
  );
  if (workspace) {
    state.scope = workspace.scope || "AI";
    $("scope-input").value = state.scope;
    if (workspace.language) {
      const option = Array.from($("language-select").options).find(
        (item) => item.value === String(workspace.language).toLowerCase()
      );
      if (option) {
        $("language-select").value = option.value;
        const config = selectedLanguageConfig();
        state.language = config.language;
        state.country = String(workspace.country || config.country).toUpperCase();
        applyInterfaceLanguage();
      }
    }
  } else {
    // 中文说明：切回未分类历史时，恢复原有的手动扫描范围与界面语言偏好。
    state.scope = state.legacyScope || "AI";
    $("scope-input").value = state.scope;
    let preferred = "en";
    try { preferred = window.localStorage.getItem("radarLanguage") || navigator.language || "en"; } catch (_) {}
    preferred = I18N.normalize(preferred);
    const option = Array.from($("language-select").options).find((item) => item.value === preferred);
    if (option) {
      $("language-select").value = preferred;
      const config = selectedLanguageConfig();
      state.language = config.language;
      state.country = config.country;
      applyInterfaceLanguage();
    }
  }
  $("detail-panel").hidden = true;
  state.selectedTopicId = null;
  state.selectedOpportunity = null;
  loadDashboard();
});

$("scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.workspaceId) {
    const workspace = state.workspaces.find((item) => item.workspace_id === state.workspaceId);
    if (workspace) $("scope-input").value = workspace.scope;
  }
  const value = $("scope-input").value.trim();
  state.scope = value || "AI";
  if (!state.workspaceId) state.legacyScope = state.scope;
  $("detail-panel").hidden = true;
  state.selectedTopicId = null;
  state.selectedOpportunity = null;
  loadDashboard();
});

$("language-select").addEventListener("change", () => {
  const config = selectedLanguageConfig();
  state.language = config.language;
  state.country = config.country;
  try { window.localStorage.setItem("radarLanguage", state.language); } catch (_) {}
  applyInterfaceLanguage();
  if (state.lastOpportunityPayload) renderOpportunities(state.lastOpportunityPayload);
  renderProviders(state.providers);
  if (state.selectedTopicId) openOpportunity(state.selectedTopicId);
});

$("run-lite-scan").addEventListener("click", async () => {
  clearError();
  const button = $("run-lite-scan");
  const status = $("scan-status");
  const scope = $("scope-input").value.trim() || "AI Agents";
  const languageConfig = selectedLanguageConfig();
  state.scope = scope;
  state.language = languageConfig.language;
  state.country = languageConfig.country;
  button.disabled = true;
  status.textContent = t("scanning");
  try {
    const payload = await postApi("/v1/lite/scan", {
      scope,
      top: 5,
      language: state.language,
      country: state.country,
      workspace_id: state.workspaceId || null,
    });
    status.textContent = tf("scanResult", {
      status: t("completed"),
      language: payload.language,
      country: payload.country,
      count: payload.cards?.length || 0,
    });
    await loadDashboard();
  } catch (error) {
    status.textContent = t("scanFailed");
    showError(tf("scanFailedMessage", { message: error.message }));
  } finally {
    button.disabled = false;
  }
});

$("generate-handoff").addEventListener("click", async () => {
  clearError();
  const button = $("generate-handoff");
  const status = $("handoff-status");
  const detail = state.selectedOpportunity;
  if (!detail) {
    status.textContent = t("openOpportunityFirst");
    return;
  }
  button.disabled = true;
  status.textContent = t("handoffGenerating");
  try {
    const payload = await postApi("/v1/lite/handoff", {
      scope: state.scope,
      rank: detail.rank,
      workspace_id: state.workspaceId || null,
    });
    status.textContent = `${t("handoffCreated")}: ${payload.handoff_path}`;
  } catch (error) {
    status.textContent = t("handoffFailed");
    showError(tf("handoffFailedMessage", { message: error.message }));
  } finally {
    button.disabled = false;
  }
});

$("close-detail").addEventListener("click", () => {
  $("detail-panel").hidden = true;
  state.selectedTopicId = null;
  state.selectedOpportunity = null;
});

(() => {
  let preferred = "en";
  try {
    preferred = window.localStorage.getItem("radarLanguage") || navigator.language || "en";
  } catch (_) {}
  preferred = I18N.normalize(preferred);
  const option = Array.from($("language-select").options).find((item) => item.value === preferred);
  if (option) $("language-select").value = preferred;
  const config = selectedLanguageConfig();
  state.language = config.language;
  state.country = config.country;
  applyInterfaceLanguage();
})();

(async () => {
  try { state.workspaceId = window.localStorage.getItem("radarWorkspaceId") || ""; } catch (_) {}
  await loadWorkspaces();
  if (state.workspaceId) {
    $("workspace-select").dispatchEvent(new Event("change"));
  } else {
    await loadDashboard();
  }
})();
