"""Radar V2 历史变化展示服务（P1 read-only preview）。

只读 tracker_runs / tracker_diffs；不会扫描 Provider、写数据或修改 V1 API。
默认仅监听 127.0.0.1:8791，和已有 Radar V1、AI Team Bridge 端口隔离。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from tracker import TrackerStore

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Radar V2 — Change Tracker</title>
  <style>
    :root {color-scheme: dark; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#0b1020; color:#e8ecf7;}
    * {box-sizing:border-box}
    body {margin:0; background:radial-gradient(ellipse at 70% 0,#172c50 0,transparent 45%),#0b1020; min-height:100vh;}
    .shell {max-width:1160px; margin:0 auto; padding:30px 26px 90px}
    .bar {display:flex; justify-content:space-between; align-items:center; gap:14px; flex-wrap:wrap}
    .brand {font-weight:750; letter-spacing:0.025em; font-size:21px}
    .mark {color:#72c7fa}
    .hint {color:#9ca9bf; font-size:12px; margin-top:5px}
    select {background:#19263d;color:#eaf2ff;border:1px solid #354761;border-radius:9px;padding:9px}
    header {margin:48px 0 28px}
    h1 {font-size:clamp(29px,4.2vw,45px); margin:0 0 13px; letter-spacing:-0.03em}
    .sub {color:#adbad0; line-height:1.65; max-width:750px}
    .flag {font-size:12px; font-weight:650; padding:7px 11px; border:1px solid #477d83; border-radius:30px; color:#9bd9d7; background:#123237; display:inline-flex; margin-bottom:19px}
    .kpis {display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin:23px 0 35px}
    .kpi {background:#151f33;border:1px solid #2c3d58;border-radius:17px;padding:21px}
    .kpi strong {display:block;font-size:35px;letter-spacing:-0.04em;margin:7px 0}
    .kpi small {color:#9eb0c7;font-size:13px}
    .kpi em {font-style:normal;color:#8dd4ed;font-size:12px}
    section {background:#131d31;border:1px solid #2c3d58;border-radius:19px;overflow:hidden;margin:20px 0}
    .section-head {display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;align-items:center;padding:22px 23px;border-bottom:1px solid #2c3d58}
    .section-head h2 {margin:0;font-size:17px}
    .metadata {color:#a8b8cf;font-size:12px}
    .line {display:grid;grid-template-columns:minmax(180px,1fr) 120px 105px 88px;gap:12px;align-items:center;padding:17px 24px;border-bottom:1px solid #263650}
    .line:last-child {border-bottom:0}
    .name {font-weight:650;overflow-wrap:anywhere}
    .id {font-size:11px;color:#8496b1;display:block;margin-top:5px;overflow-wrap:anywhere}
    .chip {display:inline-block;border-radius:8px;padding:6px 10px;font-size:12px;width:fit-content}
    .added {background:#173d31;color:#84ddb5}
    .removed {background:#3e2c2a;color:#ffb5a3}
    .shift {background:#283550;color:#a8c9ff}
    .score {color:#a9b9d3;font-size:13px}
    .note {color:#9aacc4;font-size:12px;line-height:1.8;margin:22px 5px}
    .empty {padding:28px;color:#9eafc9}
    @media(max-width:690px){.shell{padding:20px 13px 55px}.kpis{gap:8px}.kpi{padding:13px}.kpi strong{font-size:28px}.line{grid-template-columns:1fr auto;gap:7px}.score{grid-column:1/3}.section-head{padding:17px}}
  </style>
</head>
<body>
<div class="shell">
  <div class="bar">
    <div><div class="brand"><span class="mark">◉</span> RADAR <span class="mark">V2</span></div>
         <div class="hint" id="brandHint">Content Opportunity · Change Tracker</div></div>
    <select id="language" aria-label="Language">
      <option value="zh">中文</option><option value="en">English</option>
      <option value="ja">日本語</option><option value="ko">한국어</option>
    </select>
  </div>
  <header>
    <div class="flag" id="flag">历史数据回放 · 未启动实时采集</div>
    <h1 id="heading">机会变化追踪</h1>
    <div class="sub" id="subtitle">对比同一工作区相邻两次真实历史扫描的 Top-N 机会名单。</div>
  </header>
  <div class="kpis">
    <div class="kpi"><small id="scansLabel">历史扫描</small><strong id="scans">—</strong><em id="scansHint">来自现有数据库</em></div>
    <div class="kpi"><small id="addedLabel">进入名单</small><strong id="addedCount">—</strong><em id="addedHint">Top-N 变化</em></div>
    <div class="kpi"><small id="removedLabel">退出名单</small><strong id="removedCount">—</strong><em id="removedHint">不代表网络话题消失</em></div>
  </div>
  <section>
    <div class="section-head">
      <h2 id="changesHeading">机会变化记录</h2>
      <div class="metadata" id="range">—</div>
    </div>
    <div id="changes"><div class="empty">Loading…</div></div>
  </section>
  <div class="note" id="footer">注意：本页面只读，不调用任何模型或数据提供商。展示的是历史回放，而不是实时监测。</div>
</div>
<script>
  "use strict";
  const language = document.getElementById("language");
  const t = {
    zh: {flag:"历史数据回放 · 未启动实时采集",heading:"机会变化追踪",subtitle:"对比同一工作区相邻两次真实历史扫描的 Top-N 机会名单。",
      scans:"历史扫描",scansHint:"来自现有数据库",added:"进入名单",addedHint:"Top-N 变化",removed:"退出名单",removedHint:"不代表网络话题消失",
      changes:"机会变化记录",footer:"注意：本页面只读，不调用任何模型或数据提供商。展示的是历史回放，而不是实时监测。",
      new:"新进入",removedTag:"退出名单",rank_up:"排名上升",rank_down:"排名下降",score_up:"分数上升",score_down:"分数下降",duplicate_id:"重复标识",other:"数据变化",
      rank:"原排名 → 新排名",none:"无变化记录",error:"读取失败，请确认本机预览服务正常运行"},
    en: {flag:"Historical replay · No live collection",heading:"Opportunity change tracker",subtitle:"Comparing Top-N opportunities across two stored scans in one workspace.",
      scans:"Stored scans",scansHint:"From existing database",added:"Entered",addedHint:"Top-N movement",removed:"Left",removedHint:"Not deleted from the internet",
      changes:"Changes",footer:"Read-only historical replay. No provider or model API requests are made.",
      new:"Entered",removedTag:"Left Top-N",rank_up:"Rank up",rank_down:"Rank down",score_up:"Score up",score_down:"Score down",duplicate_id:"Duplicate ID",other:"Change",rank:"Previous → current",none:"No changes",error:"Unable to read local preview"},
    ja: {flag:"履歴リプレイ · リアルタイム収集なし",heading:"機会変化トラッカー",subtitle:"同一ワークスペースの2回の保存済みスキャンを比較します。",
      scans:"保存済みスキャン",scansHint:"既存データ",added:"新規ランクイン",addedHint:"Top-N の変化",removed:"ランクアウト",removedHint:"話題の消滅ではありません",
      changes:"変化の履歴",footer:"読み取り専用の履歴リプレイです。新しいAPIリクエストは行いません。",
      new:"新規",removedTag:"ランク外",rank_up:"順位上昇",rank_down:"順位下降",score_up:"スコア上昇",score_down:"スコア低下",duplicate_id:"重複ID",other:"変化",rank:"前回 → 今回",none:"変化なし",error:"データを読み込めません"},
    ko: {flag:"과거 데이터 재생 · 실시간 수집 없음",heading:"기회 변화 추적",subtitle:"동일 작업 공간의 저장된 스캔 두 회차를 비교합니다.",
      scans:"저장된 스캔",scansHint:"기존 데이터",added:"새 진입",addedHint:"Top-N 변화",removed:"목록 이탈",removedHint:"인터넷에서 사라진 것은 아님",
      changes:"변화 내역",footer:"읽기 전용 과거 데이터 재생입니다. 신규 API 요청은 없습니다.",
      new:"새 진입",removedTag:"목록 이탈",rank_up:"순위 상승",rank_down:"순위 하락",score_up:"점수 상승",score_down:"점수 하락",duplicate_id:"중복 ID",other:"변화",rank:"이전 → 현재",none:"변화 없음",error:"데이터를 불러올 수 없습니다"}
  };
  let data = null;
  function put(id, value) {document.getElementById(id).textContent = value;}
  function addNode(parent, className, value, element) {
    const node = document.createElement(element || "div");
    if (className) node.className = className;
    node.textContent = value == null ? "" : String(value);
    parent.appendChild(node);
    return node;
  }
  function draw() {
    const lang = language.value;
    const v = t[lang];
    document.documentElement.lang = lang;
    for (const item of [["flag","flag"],["heading","heading"],["subtitle","subtitle"],
        ["scansLabel","scans"],["scansHint","scansHint"],["addedLabel","added"],["addedHint","addedHint"],
        ["removedLabel","removed"],["removedHint","removedHint"],["changesHeading","changes"],["footer","footer"]]) put(item[0],v[item[1]]);
    if (!data) return;
    put("scans",data.runs.length);
    put("addedCount",data.changes.filter(x => x.change_type === "new").length);
    put("removedCount",data.changes.filter(x => x.change_type === "removed").length);
    put("range",data.runs.map(x => x.generated_at.slice(0,16).replace("T"," ")).join(" → ") + " UTC");
    const place = document.getElementById("changes");
    place.replaceChildren();
    if (data.changes.length === 0) {addNode(place,"empty",v.none);return;}
    for (const change of data.changes) {
      const line = addNode(place,"line","");
      const left = addNode(line,"","");
      addNode(left,"name",change.topic_name || change.topic_id);
      addNode(left,"id",change.topic_id);
      const kind = change.change_type;
      const chip = kind==="new"?"added":(kind==="removed"?"removed":"shift");
      addNode(line,"chip "+chip,v[kind==="new"?"new":(kind==="removed"?"removedTag":(v[kind]?kind:"other"))]);
      addNode(line,"score",v.rank);
      addNode(line,"score",String(change.prev_rank == null?"—":change.prev_rank)+" → "+String(change.curr_rank==null?"—":change.curr_rank));
    }
  }
  language.addEventListener("change",draw);
  fetch("/api/v2/summary",{cache:"no-store"})
    .then(r => {if(!r.ok)throw new Error("HTTP "+r.status);return r.json();})
    .then(d => {data=d;draw();})
    .catch(() => {data=null;put("changes",t[language.value].error);});
  draw();
</script>
</body>
</html>
"""


def read_summary(database: str | Path) -> dict:
    """读取演示库最新一次完成的对比，不进行任何写操作。"""
    path = Path(database).resolve(strict=True)
    with TrackerStore(path, read_only=True, initialize=False) as store:
        runs = list(reversed(store.list_runs(limit=2)))
        completed = [row for row in runs if row["status"] == "completed"]
        if len(completed) < 2 or len({r["workspace_id"] for r in completed}) != 1:
            raise ValueError("at_least_two_completed_runs_in_one_workspace_required")
        current = completed[-1]
        first = completed[0]
        diffs = store.list_diffs(tracker_run_id=current["tracker_run_id"])
        safe_diffs = [
            {"topic_id": row["topic_id"], "topic_name": row["topic_name"],
             "change_type": row["change_type"], "prev_rank": row["prev_rank"],
             "curr_rank": row["curr_rank"], "prev_score": row["prev_score"],
             "curr_score": row["curr_score"], "magnitude": row["magnitude"]}
            for row in diffs
        ]
        conn = store.conn
        workspace = conn.execute(
            "SELECT name,scope FROM workspaces WHERE workspace_id=?",
            (current["workspace_id"],),
        ).fetchone()
        return {
            "mode": "offline_historical_replay_not_new_collection",
            "workspace_id": current["workspace_id"],
            "workspace_name": workspace["name"] if workspace else "",
            "scope": workspace["scope"] if workspace else "",
            "runs": [
                {"tracker_run_id": row["tracker_run_id"],
                 "radar_run_id": row["radar_run_id"],
                 "generated_at": row["scheduled_at"], "status": row["status"]}
                for row in (first, current)
            ],
            "changes": safe_diffs,
            "change_count": len(safe_diffs),
            "provider_api_calls_during_view": 0,
        }


def create_server(database: str | Path, host: str = "127.0.0.1",
                  port: int = 8791) -> ThreadingHTTPServer:
    """创建仅使用已知固定路由的本机 HTTP 只读服务。"""
    db_path = Path(database).resolve(strict=True)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.respond_json({"status": "ok", "read_only": True})
                return
            if path == "/api/v2/summary":
                try:
                    self.respond_json(read_summary(db_path))
                except (ValueError, sqlite3.Error, OSError):
                    self.respond_json({"error": "preview_database_unavailable"}, status=503)
                return
            if path in ("/", "/index.html"):
                self.respond(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
                return
            self.respond_json({"error": "not_found"}, status=404)

        def respond_json(self, content, status=200):
            self.respond(status, "application/json; charset=utf-8",
                         json.dumps(content, ensure_ascii=False).encode("utf-8"))

        def respond(self, status, mime, payload):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            """本地开发预览不打印 URL 参数或请求正文。"""
            return

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    with create_server(args.database, port=args.port) as server:
        print("Radar V2 read-only preview: http://127.0.0.1:" + str(server.server_port),
              flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
