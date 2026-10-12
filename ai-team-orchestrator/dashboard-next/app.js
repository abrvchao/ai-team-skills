/* AI Team Dashboard v2. Read-only browser; never manufactures task progress. */
(() => {
  "use strict";
  const D = window.DashboardData;
  if (!D) return;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const $ = (id) => document.getElementById(id);
  const copy = {
    zh: {
      brandSub:"协作总控 · Control Center v2", classic:"经典面板",language:"语言",
      refresh:"立即刷新",heading:"项目运行总览",
      intro:"项目进度、子任务、Worker 执行和数据源接入，均基于真实接口。",
      projects:"项目与子任务进度",workers:"Worker 工作负载",sources:"数据源接入计划",
      all:"全部",running:"执行中",blocked:"阻塞/失败",done:"已完成",
      progressNotice:"项目百分比为 Supervisor 流程进度；子任务百分比只来自 Worker 上报。未知不等于 0%。",
      sourcesNotice:"路线图中“已排期”不等于 Bridge 已派工，更不等于真实平台采集成功。",
      footer:"只读 Dashboard v2 · Bridge 不可用时，显示上次读取的数据并明确标记为历史快照。",
      fetching:"正在读取实时数据…", connected:"Bridge 实时连接正常", bridgeDown:"Bridge 不可用：展示上次读取的历史数据，勿当成实时状态。",
      apiError:"监控接口暂不可用：数据为历史快照，未连接时不显示为实时在线。",
      statusError:"基础信息接口不可用，数据源计划可能是旧记录。",
      updated:"最近成功读取",notRead:"尚未成功读取", stale:"历史快照",live:"实时",
      projectsKpi:"项目运行记录",workersKpi:"在线 Worker",busyKpi:"忙碌 Worker",blockedKpi:"阻塞项目",
      flow:"流程进度",reported:"上报进度",unknownProgress:"进度未上报",
      phase:"阶段",tasks:"子任务", noTasks:"无已记录子任务",
      blockers:"阻塞原因",noProjects:"暂无可验证的项目记录。",noWorkers:"暂无可验证的 Worker 记录。",
      online:"在线",offline:"离线/未知",idle:"空闲",busy:"执行中",
      sample:"最近任务抽样",notKnown:"未知",
      sourcesUnavailable:"数据源计划尚未从本机服务加载；不能据此判断尚未安排。",
      sourceStages:"阶段安排",sourceWorkers:"分工安排",scheduled:"计划状态",
      noMore:"最近没有已记录的任务", statusMap: {
        running:"执行中", claimed:"已领取",assigned:"已分配",completed:"已完成",complete:"已完成",
        accepted:"已接受",accept:"已接受",passed:"已通过",blocked:"阻塞",failed:"失败",
        error:"错误",queued:"排队中",pending:"等待中",cancelled:"已取消",unknown:"未知",
        primary_running:"主任务中",review_running:"交叉审查中",validation_running:"验收中",
      },
    },
    en: {
      brandSub:"Multi-agent Control Center v2",classic:"Classic dashboard",language:"Language",
      refresh:"Refresh now",heading:"Execution overview",intro:"Projects, tasks, worker load and sources. Evidence, not estimates.",
      projects:"Project and task progress",workers:"Worker workload",sources:"Source onboarding plan",
      all:"All",running:"Active",blocked:"Blocked / Failed",done:"Completed",
      progressNotice:"Project percentages are Supervisor workflow progress. Task percentages require a real Worker event. Unknown is not 0%.",
      sourcesNotice:"Scheduled does not mean dispatched through Bridge or verified against a platform.",
      footer:"Read-only Dashboard v2 · Bridge outages show a prominently marked cached snapshot.",
      fetching:"Reading live data…",connected:"Bridge is responding",bridgeDown:"Bridge is unavailable: showing an earlier snapshot, not live status.",
      apiError:"Monitor unavailable: showing cached values; do not interpret these as live.",
      statusError:"Status endpoint is unavailable; source plan may be stale.",
      updated:"Last successful read",notRead:"No successful read",stale:"Snapshot",live:"Live",
      projectsKpi:"Project runs",workersKpi:"Workers online",busyKpi:"Workers busy",blockedKpi:"Blocked runs",
      flow:"Workflow",reported:"Reported",unknownProgress:"No reported percentage",
      phase:"Phase",tasks:"Child tasks",noTasks:"No recorded child tasks",
      blockers:"Blockers",noProjects:"No verified project runs available.",noWorkers:"No verified worker data available.",
      online:"Online",offline:"Offline / Unknown",idle:"Idle",busy:"Running",
      sample:"Recent task sample",notKnown:"Unknown",
      sourcesUnavailable:"Source plan is not supplied by this service; scheduling cannot be inferred.",
      sourceStages:"Stages",sourceWorkers:"Owners",scheduled:"Planned state",noMore:"No recent recorded tasks",
      statusMap:{running:"Running",claimed:"Claimed",assigned:"Assigned",completed:"Completed",complete:"Complete",
        accepted:"Accepted",accept:"Accepted",passed:"Passed",blocked:"Blocked",failed:"Failed",
        error:"Error",queued:"Queued",pending:"Pending",cancelled:"Cancelled",unknown:"Unknown",
        primary_running:"Primary active",review_running:"Review active",validation_running:"Validation active"},
    },
    ja: {
      brandSub:"AI チーム統合管理 v2",classic:"従来の画面",language:"言語",
      refresh:"更新",heading:"プロジェクトの実行状況",intro:"実データに基づく進捗・タスク・Worker・データソース。",
      projects:"プロジェクトとサブタスク",workers:"Worker の稼働状況",sources:"データソース導入計画",
      all:"すべて",running:"実行中",blocked:"停止・失敗",done:"完了",
      progressNotice:"プロジェクトの％はワークフロー進捗です。タスクの％は実際の報告のみ表示します。",
      sourcesNotice:"予定は実際の割当てや収集成功を意味しません。",
      footer:"読み取り専用 v2 · Bridge 障害中のデータは過去のスナップショットです。",
      fetching:"データを取得中…",connected:"Bridge に接続中",bridgeDown:"Bridge に接続できません。過去の記録を表示中。",
      apiError:"監視 API が利用できません。以前の記録です。",statusError:"状態 API が利用できません。",
      updated:"最終更新",notRead:"データ未取得",stale:"過去データ",live:"リアルタイム",
      projectsKpi:"プロジェクト",workersKpi:"オンライン",busyKpi:"実行中 Worker",blockedKpi:"停止した案件",
      flow:"フロー",reported:"報告済み",unknownProgress:"進捗未報告",phase:"段階",tasks:"サブタスク",
      noTasks:"記録なし",blockers:"障害",noProjects:"プロジェクトデータなし",noWorkers:"Worker データなし",
      online:"オンライン",offline:"オフライン/不明",idle:"待機",busy:"実行中",
      sample:"最近のタスク",notKnown:"不明",sourcesUnavailable:"導入計画のデータがありません。",
      sourceStages:"段階",sourceWorkers:"担当",scheduled:"予定状態",noMore:"タスク記録なし",
      statusMap:{running:"実行中",claimed:"担当中",assigned:"割当済み",completed:"完了",blocked:"停止",
        failed:"失敗",queued:"待機",pending:"待機",accepted:"承認済み",unknown:"不明"},
    },
    ko: {
      brandSub:"AI 팀 통합 관제 v2",classic:"기존 대시보드",language:"언어",
      refresh:"새로고침",heading:"프로젝트 실행 현황",intro:"실제 API 기반 프로젝트, 작업, Worker 및 데이터 소스 정보.",
      projects:"프로젝트 및 하위 작업",workers:"Worker 실행 현황",sources:"데이터 소스 도입 계획",
      all:"전체",running:"진행 중",blocked:"차단/실패",done:"완료",
      progressNotice:"프로젝트 백분율은 업무 흐름 기준입니다. 작업 진행률은 실제 Worker 보고에만 근거합니다.",
      sourcesNotice:"계획은 실제 작업 할당이나 데이터 수집 성공을 의미하지 않습니다.",
      footer:"읽기 전용 v2 · Bridge 중단 시 과거 스냅샷으로 표시합니다.",
      fetching:"데이터를 읽는 중…",connected:"Bridge 연결 정상",bridgeDown:"Bridge 접속 불가: 이전 데이터 표시 중.",
      apiError:"모니터 API 연결 실패: 캐시된 데이터입니다.",statusError:"상태 API 연결 실패.",
      updated:"마지막 갱신",notRead:"데이터 없음",stale:"과거 기록",live:"실시간",
      projectsKpi:"프로젝트",workersKpi:"온라인 Worker",busyKpi:"실행 Worker",blockedKpi:"차단 프로젝트",
      flow:"단계",reported:"보고 진행률",unknownProgress:"진행률 미보고",phase:"단계",tasks:"하위 작업",
      noTasks:"기록 없음",blockers:"차단 사유",noProjects:"프로젝트 기록 없음",noWorkers:"Worker 기록 없음",
      online:"온라인",offline:"오프라인/알 수 없음",idle:"대기",busy:"실행 중",
      sample:"최근 작업 샘플",notKnown:"알 수 없음",sourcesUnavailable:"데이터 소스 계획을 읽을 수 없습니다.",
      sourceStages:"단계",sourceWorkers:"담당",scheduled:"계획 상태",noMore:"최근 작업 없음",
      statusMap:{running:"실행 중",claimed:"작업 중",assigned:"할당됨",completed:"완료",blocked:"차단",
        failed:"실패",queued:"대기",pending:"대기",accepted:"승인",unknown:"알 수 없음"},
    },
  };
  const state = {
    lang: (/^(zh|en|ja|ko)/.exec(navigator.language || "zh") || ["zh"])[0],
    filter: "all", monitor: null, status: null,
    monitorReadAt: null, statusReadAt: null,
    monitorError: null, statusError: null, refreshing: false,
  };
  const T = (key) => copy[state.lang][key] ?? copy.en[key] ?? key;
  const fmt = (date) => date ? new Intl.DateTimeFormat(state.lang, {
    hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false,
  }).format(date) : T("notRead");
  const kind = (status) => D.statusKind(status);
  const label = (status) => copy[state.lang].statusMap[status] ||
    copy.en.statusMap[status] || D.safeText(status);
  function statusTag(status) {
    const tag = el("span","tag "+kind(status), label(status));
    return tag;
  }
  function setText(id, value) { $(id).textContent = value; }
  function progress(value, description) {
    const wrapper = el("div","progress-area");
    if (value === null) { wrapper.append(el("span","progress-unknown",T("unknownProgress"))); return wrapper; }
    const title = el("div","compact",description + " · " + value + "%");
    const track = el("div","track"); track.setAttribute("role","progressbar");
    track.setAttribute("aria-label",description);
    track.setAttribute("aria-valuemin","0");
    track.setAttribute("aria-valuemax","100");
    track.setAttribute("aria-valuenow",String(value));
    const fill = el("div","fill"); fill.style.width = value + "%";
    track.append(fill); wrapper.append(title,track); return wrapper;
  }
  function statusPanelRow(title, description, value) {
    const row = el("div","row");
    const first = el("div");
    first.append(el("span","row-label",title));
    if (description) first.append(el("span","smallnote",description));
    row.append(first,value); return row;
  }
  function renderKpis() {
    const root = $("stats"); root.replaceChildren();
    const view = state.monitor ? D.summary(state.monitor) : null;
    // Never advertise worker online status as live when Bridge does not respond.
    const live = view && !state.monitorError && view.bridgeAvailable;
    const metrics = [
      [T("projectsKpi"), view ? view.knownProjects : "—", T("flow")],
      [T("workersKpi"), live ? view.workersOnline+"/"+view.workersTotal : "—", live ? T("live") : T("stale")],
      [T("busyKpi"), live ? view.workersBusy : "—", live ? T("live") : T("stale")],
      [T("blockedKpi"), view ? view.blockedProjects : "—", live ? T("live") : T("stale")],
    ];
    for (const [labelText, value, foot] of metrics) {
      const card = el("article","kpi");
      card.append(el("div","kpi-label",labelText),el("div","kpi-value",value),el("div","kpi-foot",foot));
      root.append(card);
    }
  }
  function renderConnection() {
    const banner = $("connectivity");
    let cls = "good", message = T("connected");
    if (!state.monitor || state.monitorError) { cls="bad"; message=T("apiError"); }
    else if (!state.monitor.bridgeAvailable) { cls="warn"; message=T("bridgeDown"); }
    else if (state.statusError) { cls="warn"; message=T("statusError"); }
    banner.className = "banner "+cls;
    const dot = el("span","dot "+cls); dot.setAttribute("aria-hidden","true");
    banner.replaceChildren(dot,el("span","",message));
    const timestamp = state.monitorReadAt
      ? T("updated") + " " + fmt(state.monitorReadAt) + (cls==="good" ? " · "+T("live") : " · "+T("stale"))
      : T("notRead");
    setText("updated",timestamp);
  }
  function renderTasks(tasks) {
    const box = el("div");
    box.append(el("h3","",T("tasks")));
    if (!tasks.length) { box.append(el("p","mini",T("noTasks"))); return box; }
    for (const t of tasks) {
      box.append(statusPanelRow(
        t.title,
        [t.id, t.worker, t.phase].filter(x => x && x!=="—").join(" · "),
        (() => {
          const detail = el("div"); detail.append(statusTag(t.status));
          detail.append(progress(t.progress,T("reported")));
          return detail;
        })(),
      ));
    }
    return box;
  }
  function renderProjects() {
    const container = $("projects"); container.replaceChildren();
    const view = state.monitor ? D.normalizeMonitor(state.monitor) : null;
    let runs = view ? view.runs : [];
    if (state.filter !== "all") runs = runs.filter(r => kind(r.status) === state.filter);
    if (!runs.length) { container.append(el("div","empty",T("noProjects"))); return; }
    for (const r of runs) {
      const details = el("details","panel");
      if (kind(r.status) === "failed" || kind(r.status) === "running") details.open = true;
      const summary = el("summary","panel-head");
      const left = el("div");
      const title = el("div","title-line");
      title.append(el("strong","",r.name),statusTag(r.status));
      left.append(title,el("div","mini",r.id));
      summary.append(left,progress(r.progress,T("flow")));
      details.append(summary);
      const body = el("div","panel-body");
      if (r.blockers.length) {
        const note = el("div","note");
        note.append(el("strong","",T("blockers")+": "));
        note.append(document.createTextNode(r.blockers.join("; ")));
        body.append(note);
      }
      if (r.phases.length) {
        const phaseBox = el("div");
        phaseBox.append(el("h3","",T("phase")));
        for (const ph of r.phases) {
          const wrapper = el("div");
          wrapper.append(statusTag(ph.status));
          wrapper.append(progress(ph.progress,T("flow")));
          phaseBox.append(statusPanelRow(ph.name,"",wrapper));
        }
        body.append(phaseBox);
      }
      body.append(renderTasks(r.tasks));
      details.append(body); container.append(details);
    }
  }
  function renderWorkers() {
    const root = $("workers"); root.replaceChildren();
    const view = state.monitor ? D.normalizeMonitor(state.monitor) : null;
    setText("worker-sample",view && view.taskSampleSize !== null
      ? T("sample")+": "+view.taskSampleSize : T("sample")+": "+T("notKnown"));
    if (!view || !view.workers.length) {root.append(el("div","empty",T("noWorkers")));return;}
    for (const worker of view.workers) {
      const card = el("article","panel worker-card");
      // A cached worker record must never be presented as currently online.
      const live = !state.monitorError && state.monitor && state.monitor.bridge_available === true;
      const verifiedOnline = live && worker.online;
      const title = el("div","worker-title");
      title.append(el("strong","",worker.id),
        el("span","tag "+(verifiedOnline?"done":"failed"),
          !live?T("stale"):verifiedOnline?T("online"):T("offline")));
      card.append(title);
      const load = !live?T("stale"):worker.busy?T("busy"):verifiedOnline?T("idle"):T("offline");
      const count = !live||worker.activeCount===null?T("notKnown"):String(worker.activeCount);
      card.append(el("div","mini",load+" · "+T("tasks")+": "+count));
      const tasks = el("div","worker-tasks");
      if (!worker.tasks.length) tasks.append(el("p","mini",T("noMore")));
      for (const t of worker.tasks.slice(0,4)) {
        const end = el("div");
        end.append(statusTag(t.status)); end.append(progress(t.progress,T("reported")));
        tasks.append(statusPanelRow(t.title,t.id,end));
      }
      card.append(tasks); root.append(card);
    }
  }
  function renderSources() {
    const root = $("sources"); root.replaceChildren();
    const source = state.status && state.status.social_sources;
    if (!source || typeof source !== "object") {
      root.append(el("div","empty",T("sourcesUnavailable"))); return;
    }
    root.append(el("p","mini",D.safeText(source.name)+" · "+D.safeText(source.status)));
    const stages = el("div");
    stages.append(el("h3","",T("sourceStages")));
    for (const s of Array.isArray(source.phases)?source.phases.slice(0,10):[]) {
      stages.append(statusPanelRow(D.safeText(s.id)+" · "+D.safeText(s.goal),
        D.safeText(s.window,""),el("span","tag pending",D.safeText(s.status))));
    }
    const platforms = el("div"); platforms.append(el("h3","",T("sources")));
    for (const s of Array.isArray(source.platforms)?source.platforms.slice(0,10):[]) {
      platforms.append(statusPanelRow(D.safeText(s.name),D.safeText(s.priority,""),
        el("span","tag pending",D.safeText(s.status))));
    }
    const owners = el("div"); owners.append(el("h3","",T("sourceWorkers")));
    for (const t of Array.isArray(source.work_packages)?source.work_packages.slice(0,16):[]) {
      owners.append(statusPanelRow(D.safeText(t.title),
        D.safeText(t.owner)+" · "+D.safeText(t.id),el("span","tag pending",D.safeText(t.status))));
    }
    root.append(platforms,stages,owners);
  }
  function translate() {
    document.documentElement.lang = ({zh:"zh-CN",en:"en",ja:"ja",ko:"ko"})[state.lang];
    for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = T(node.dataset.i18n);
    render();
  }
  function render() {renderConnection();renderKpis();renderProjects();renderWorkers();renderSources();}
  async function jsonRequest(path) {
    const ctrl = new AbortController();
    const watchdog = setTimeout(()=>ctrl.abort(),6000);
    try {
      const response = await fetch(path,{cache:"no-store",credentials:"same-origin",signal:ctrl.signal});
      if (!response.ok) throw new Error("HTTP "+response.status);
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Invalid JSON");
      return data;
    } finally {clearTimeout(watchdog);}
  }
  async function refresh() {
    if (state.refreshing) return;
    state.refreshing = true; $("refresh").disabled = true;
    try {
      const [monitor,status] = await Promise.allSettled([
        jsonRequest("/api/monitor"),jsonRequest("/api/status"),
      ]);
      if (monitor.status === "fulfilled") {
        state.monitor=monitor.value;
        state.monitorReadAt=new Date();
        state.monitorError=null;
      } else state.monitorError=monitor.reason;
      if (status.status === "fulfilled") {
        state.status=status.value;
        state.statusReadAt=new Date();
        state.statusError=null;
      } else state.statusError=status.reason;
    } finally {
      state.refreshing=false; $("refresh").disabled=false; render();
    }
  }
  $("lang").value = state.lang;
  $("lang").addEventListener("change",(event)=>{
    const next=event.target.value;
    if (D.LANGUAGES.includes(next)) {state.lang=next; translate();}
  });
  for(const button of $("filters").querySelectorAll("button[data-filter]")) {
    button.addEventListener("click",()=>{
      state.filter=button.dataset.filter;
      for (const peer of $("filters").querySelectorAll("button[data-filter]")) {
        peer.setAttribute("aria-pressed",String(peer===button));
      }
      renderProjects();
    });
  }
  $("refresh").addEventListener("click",refresh);
  translate();
  refresh();
  setInterval(refresh,12000);
})();
