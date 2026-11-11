#!/usr/bin/env python3
"""AI Team local project dashboard（AI Team 本地项目进度面板）.

只读聚合现有 truth sources（真相源），不创建新的平行状态数据库。
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import re
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from supervisor import BridgeClient
from team_supervisor import TeamSupervisor
from team_monitor import build_monitor
from product_intake import ProductContractStore

ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE = ROOT / "docs" / "AI_TEAM_STATUS.md"
BACKLOG_FILE = ROOT / "docs" / "BACKLOG.md"
PRODUCT_PROGRESS_FILE = ROOT / "content-opportunity-radar" / "PRODUCT_PROGRESS.md"
R3_READINESS_FILE = ROOT / "content-opportunity-radar" / ".radar" / "r3" / "R3_READINESS.json"
RUN_STATE_DIR = Path(__file__).resolve().parent / ".orchestrator" / "team-runs"
CONTRACT_DIR = Path(__file__).resolve().parent / ".orchestrator" / "product-contracts"


def _table_rows(text: str, prefix: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.startswith(f"| {prefix}"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def _program_state() -> dict[str, Any]:
    text = STATUS_FILE.read_text(encoding="utf-8") if STATUS_FILE.is_file() else ""
    milestones = []
    for key in ("M0", "M1", "M2", "M3"):
        for row in _table_rows(text, key):
            if len(row) >= 4:
                milestones.append({
                    "id": key,
                    "name": row[0],
                    "checklist": row[1],
                    "percent": row[2],
                    "state": row[3],
                })
                break
    weighted = re.search(r"Weighted V1 roll-up:\s*\*\*([^*]+)\*\*", text)
    return {
        "milestones": milestones,
        "weighted_progress": weighted.group(1).strip() if weighted else "unknown（未知）",
    }


def _product_state() -> dict[str, Any]:
    text = PRODUCT_PROGRESS_FILE.read_text(encoding="utf-8") if PRODUCT_PROGRESS_FILE.is_file() else ""
    overall = re.search(r"Checklist roll-up:\s*\*\*([^*]+)\*\*", text)
    gate = re.search(r"Current gate:\s*\*\*([^*]+)\*\*", text)
    status = re.search(r"Overall status:\s*\*\*([^*]+)\*\*", text)
    blocker = re.search(r"Current blocker:\s*(.+)", text)
    stages = []
    for key in ("R0", "R1", "R2", "R3", "R4"):
        for row in _table_rows(text, key):
            if len(row) >= 4:
                stages.append({"stage": row[0], "checklist": row[1], "progress": row[2], "state": row[3]})
                break
    return {
        "name": "Content Opportunity Radar（内容机会雷达）",
        "overall": overall.group(1).strip() if overall else "unknown（未知）",
        "gate": gate.group(1).strip() if gate else "unknown（未知）",
        "status": status.group(1).strip() if status else "unknown（未知）",
        "blocker": blocker.group(1).strip() if blocker else "none（无）",
        "stages": stages,
    }


def _r3_readiness_state() -> dict[str, Any]:
    if not R3_READINESS_FILE.is_file():
        return {
            "available": False,
            "preparation_complete": False,
            "stage_progress": "unknown（未知）",
            "stage_percent": 0,
            "r3_complete": False,
            "gates": [],
            "next_actions": ["generate R3 readiness（生成 R3 就绪度）"],
        }
    try:
        payload = json.loads(R3_READINESS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "available": False,
            "preparation_complete": False,
            "stage_progress": "error（错误）",
            "stage_percent": 0,
            "r3_complete": False,
            "gates": [],
            "next_actions": [f"repair readiness file（修复就绪度文件）: {exc}"],
        }
    # 中文说明：Dashboard 只投影 readiness 真相源，不在这里重新计算 R3 是否通过。
    return {"available": True, **payload}


def _backlog_state() -> list[dict[str, str]]:
    text = BACKLOG_FILE.read_text(encoding="utf-8") if BACKLOG_FILE.is_file() else ""
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("| M2-") and not line.startswith("| M3-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3:
            rows.append({"id": cells[0], "item": cells[1], "state": cells[2]})
    return rows


def _contracts_state(limit: int = 8) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not CONTRACT_DIR.is_dir():
        return rows
    # 中文说明：合同状态直接读取 ProductContractStore 的 JSON 真相源，不在 Dashboard 复制一份状态。
    for path in sorted(CONTRACT_DIR.glob("contract_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "contract_id": contract.get("contract_id"),
            "product_name": contract.get("product_name"),
            "idea": contract.get("idea"),
            "status": contract.get("status"),
            "approval_required": bool(contract.get("approval_required")),
            "approved": bool(contract.get("approved")),
            "run_id": contract.get("run_id"),
            "created_at": contract.get("created_at"),
        })
    return rows


def _latest_runs(limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not RUN_STATE_DIR.is_dir():
        return rows
    # 中文说明：按状态文件 mtime 排序即可表达最近活动，不依赖 run id 的随机部分。
    for path in sorted(RUN_STATE_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        progress = dict(run.get("progress") or {})
        artifacts = []
        for value in (
            run.get("final_artifact"),
            (run.get("implementation") or {}).get("artifact"),
            (run.get("validation") or {}).get("artifact"),
        ):
            if value and value not in artifacts:
                artifacts.append(value)
        for repair in run.get("repairs") or []:
            value = repair.get("artifact")
            if value and value not in artifacts:
                artifacts.append(value)
        # 中文说明：发布报告是面向人的最终入口，存在时应比原始 task log 更容易被发现。
        release_report = Path(run.get("run_root") or "") / "release_report.md"
        if release_report.is_file() and str(release_report) not in artifacts:
            artifacts.append(str(release_report))
        rows.append({
            "run_id": run.get("run_id"),
            "goal": run.get("goal"),
            "status": run.get("status"),
            "phase": run.get("phase"),
            "overall_percent": progress.get("overall_percent", 0),
            "schedule_state": (progress.get("schedule") or {}).get("state") or "unknown",
            "project_due_at": (progress.get("schedule") or {}).get("project_due_at"),
            "blockers": progress.get("blockers") or run.get("errors") or [],
            "next_action": progress.get("next_action") or "inspect run（检查运行）",
            "handoffs": progress.get("handoffs") or {
                "total": 0, "pending": 0, "in_progress": 0,
                "delivered": 0, "failed": 0, "latest": [],
            },
            "artifacts": artifacts,
            "updated_at": run.get("updated_at"),
        })
    return rows


def _worker_changes(limit: int = 20) -> list[dict[str, Any]]:
    """只读查看最近的人员变动，绝不暴露 Worker 详细配置或密钥。"""
    path = RUN_STATE_DIR / "_worker-roster-events.jsonl"
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8") as stream:
            lines = deque(stream, maxlen=max(1, min(limit, 30)))
        items = []
        for line in lines:
            event = json.loads(line)
            items.append({
                "agent_id": str(event.get("agent_id") or ""),
                "kind": str(event.get("kind") or ""),
                "detected_at": str(event.get("detected_at") or ""),
            })
        return list(reversed(items))
    except (OSError, ValueError, TypeError):
        return []  # 无法核验时按暂无显示，不编造变更。


def _agents(bridge_url: str) -> list[dict[str, Any]]:
    try:
        items = BridgeClient(bridge_url).agents()
    except Exception as exc:
        return [{"agent_id": "bridge", "available": False, "configured": False, "reason": str(exc)}]
    # 直接显示 Bridge 登记的全部 Agent：MiMo/NVIDIA 等不能被旧 UI 白名单隐藏。
    # 注意 configured 不等于 online；只有 available=true 才显示在线。
    return items


def _recent_tasks(bridge_url: str, limit: int = 24) -> dict[str, Any]:
    """真实 Bridge 任务列表，只投影展示必需字段，不向浏览器暴露 prompt 或密钥。"""
    try:
        tasks = BridgeClient(bridge_url).tasks()
    except Exception:
        return {"available": False, "items": [], "reason": "bridge_unreachable"}
    tasks.sort(key=lambda x: (x.get("created_at") or "", x.get("task_id") or ""), reverse=True)
    return {
        "available": True,
        "items": [
            {
                "task_id": row.get("task_id"),
                "agent_id": row.get("agent_id"),
                "title": row.get("title"),
                "status": row.get("status"),
                "started": bool(row.get("started")),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "worker_id": row.get("worker_id"),
            }
            for row in tasks[:max(1, min(limit, 50))]
        ],
    }


def _task_detail(bridge_url: str, task_id: str) -> dict[str, Any]:
    """按需读取进度事件、产物。请求始终只读，不运行任务。"""
    if not re.fullmatch(r"task_[A-Za-z0-9_-]{1,80}", task_id):
        raise ValueError("invalid_task_id")
    client = BridgeClient(bridge_url)
    current = client.task(task_id)
    progress = client.progress(task_id)
    artifacts = client.artifacts(task_id)
    return {
        "task_id": task_id,
        "status": current.get("status", "unknown"),
        "started": bool(current.get("started")),
        "progress": [
            {key: row.get(key) for key in ("stage", "message", "current", "total", "percent", "created_at")}
            for row in progress[-40:]
        ],
        "artifacts": [
            {key: row.get(key) for key in ("artifact_id", "kind", "label", "path", "created_at")}
            for row in artifacts[-30:]
        ],
    }


def _social_source_expansion() -> dict[str, Any]:
    """只读展示新数据源路线图；计划不等于已经派工或实施。"""
    path = Path(__file__).resolve().parent / "tasks/china-social-source-expansion-20261012.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "name": str(data.get("name") or "")[:140],
            "status": str(data.get("overall_status") or "")[:60],
            "platforms": [
                {"name": str(x.get("name") or "")[:45],
                 "priority": str(x.get("priority") or "")[:10],
                 "status": str(x.get("status") or "")[:85]}
                for x in data.get("platforms", [])[:10]],
            "phases": [
                {"id": str(x.get("id") or "")[:15],
                 "goal": str(x.get("goal") or "")[:180],
                 "window": str(x.get("window") or "")[:75],
                 "status": str(x.get("status") or "")[:85]}
                for x in data.get("phases", [])[:8]],
            "work_packages": [
                {"id": str(x.get("task_id") or "")[:45],
                 "owner": str(x.get("owner") or "")[:45],
                 "title": str(x.get("title") or "")[:150],
                 "status": str(x.get("state") or "")[:85]}
                for x in data.get("work_packages", [])[:12]],
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {"name": "社交数据源扩展", "status": "plan_unavailable",
                "platforms": [], "phases": [], "work_packages": []}


def build_state(bridge_url: str = "http://127.0.0.1:8765") -> dict[str, Any]:
    """聚合只读状态；任一来源失败都应降级显示，而不是让整个 dashboard 崩溃。"""
    return {
        "program": _program_state(),
        "product": _product_state(),
        "r3_readiness": _r3_readiness_state(),
        "social_sources": _social_source_expansion(),
        "backlog": _backlog_state(),
        "contracts": _contracts_state(),
        "agents": _agents(bridge_url),
        "runs": _latest_runs(),
        "worker_changes": _worker_changes(),
    }


def _monitor_state(bridge_url: str = "http://127.0.0.1:8765") -> dict[str, Any]:
    """按需聚合运行中任务、历史子任务和每个 Worker 的当前进度。"""
    agents = _agents(bridge_url)
    return build_monitor(
        BridgeClient(bridge_url, timeout=6),
        RUN_STATE_DIR,
        agents,
    )


HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Team Project Dashboard（项目进度面板）</title>
<style>
:root{color-scheme:dark;--bg:#0b0d12;--panel:#141923;--line:#293142;--text:#eef3fa;--muted:#9aa8ba;--ok:#65d58a;--warn:#f4c35f;--bad:#ff7676;--accent:#79a8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
main{width:min(1440px,calc(100% - 32px));margin:24px auto 56px}.hero{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:20px}.hero h1{font-size:30px;margin:0}.hero p{color:var(--muted);margin:6px 0 0}.pill{padding:8px 12px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.span4{grid-column:span 4}.span6{grid-column:span 6}.span8{grid-column:span 8}.span12{grid-column:span 12}h2{font-size:16px;margin:0 0 12px}h3{font-size:14px;margin:12px 0 6px}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid rgba(255,255,255,.05)}.row:first-of-type{border-top:0}.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.bar{height:8px;background:#202737;border-radius:99px;overflow:hidden;margin-top:6px}.bar>i{display:block;height:100%;background:var(--accent)}.agent{display:inline-flex;gap:7px;align-items:center;margin:4px 5px 4px 0;padding:6px 9px;border:1px solid var(--line);border-radius:10px}.actions{display:flex;gap:8px;margin-top:8px}.actions button{border:1px solid var(--line);background:#202737;color:var(--text);padding:7px 10px;border-radius:8px;cursor:pointer}.actions button:hover{border-color:var(--accent)}.dot{width:8px;height:8px;border-radius:50%;background:var(--bad)}.dot.on{background:var(--ok)}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#c7d8ff}.run{padding:11px 0;border-top:1px solid rgba(255,255,255,.06)}.run:first-of-type{border-top:0}.small{font-size:12px}.artifact{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);max-width:100%}@media(max-width:900px){.span4,.span6,.span8{grid-column:span 12}.hero{align-items:start;flex-direction:column}}
</style>
<style>
/* 项目、子任务与 Worker 的可折叠实时进度面板。 */
.monitor-run{border:1px solid var(--line);border-radius:12px;margin:10px 0;background:#111a29;overflow:hidden}
.monitor-run>summary{padding:13px 15px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:16px}
.monitor-summary-title{min-width:0;flex:1}.monitor-name{font-weight:650;color:var(--text);overflow-wrap:anywhere}
.monitor-summary-progress{flex:0 0 180px}.monitor-run-body{border-top:1px solid var(--line);padding:12px 16px 14px}
.monitor-phase{border-top:1px solid #283346;padding:12px 0}
.monitor-phase-header{display:flex;align-items:center;justify-content:space-between;gap:12px}
.monitor-phase-header>.monitor-progress{width:180px;flex-shrink:0}
.monitor-task{border-top:1px solid #263041;display:flex;justify-content:space-between;gap:12px;padding:10px 4px;align-items:start}
.monitor-task-info{min-width:0;flex:1;overflow-wrap:anywhere}
.monitor-task-status{width:138px;flex-shrink:0;text-align:right}
.monitor-link{border:0;background:none;color:#b6d3ff;cursor:pointer;padding:0;text-align:left;overflow-wrap:anywhere;font:inherit;font-weight:600}
.monitor-link:hover{text-decoration:underline}
.monitor-progress{display:flex;flex-direction:column;gap:4px}
.monitor-track{height:6px;width:100%;border-radius:50px;background:#29344a;overflow:hidden}
.monitor-fill{background:#72a4ff;height:100%;border-radius:50px}
.monitor-worker-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,315px),1fr));gap:12px}
.monitor-worker{background:#101a2a;border:1px solid var(--line);border-radius:12px;padding:13px;min-width:0}
.monitor-worker-header{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:7px}
.monitor-worker .monitor-task-status{width:90px}.monitor-worker .monitor-task{margin-top:7px}
@media(max-width:900px){.monitor-run>summary{flex-direction:column;align-items:stretch}
.monitor-summary-progress{flex-basis:auto}.monitor-phase-header{flex-direction:column;align-items:stretch}
.monitor-phase-header>.monitor-progress{width:100%}.monitor-task-status{width:95px}}
</style>
</head><body><main>
<div class="hero"><div><h1>AI Team Project Dashboard（项目进度面板）</h1><p>Program（总计划）→ Product（产品）→ Run（运行）→ Worker（执行器）统一真相视图</p></div><div id="refresh" class="pill">loading（加载中）</div></div>
<div class="grid">
<section class="card span8"><h2>V1 Program（V1 总计划）</h2><div id="program"></div></section>
<section class="card span4"><h2>Workers & Execution Surfaces（模型执行器与执行面）</h2><div id="agents"></div></section>
<section class="card span12"><h2>Live Project Execution（实时项目与子任务进度）</h2><div class="small muted">项目阶段百分比来自 Supervisor；子任务百分比只显示 Worker 实际上报。点击项目可展开每个阶段和任务。</div><div id="monitor-runs" style="margin-top:12px">加载中…</div></section>
<section class="card span12"><h2>Worker Workload（Worker 当前工作与进度）</h2><div class="small muted">状态、当前任务、进度事件、最近完成/失败记录；离线、空闲与缺少进度数据分开显示。</div><div id="monitor-workers" style="margin-top:12px">加载中…</div></section>
<section class="card span6"><h2>Current Product（当前产品）</h2><div id="product"></div></section>
<section class="card span6"><h2>Radar R3 Readiness（Radar R3 就绪度）</h2><div id="r3"></div></section>
<section class="card span12"><h2>Radar Social Sources（社交数据源接入日程）</h2>
<div class="small muted">官方 API 优先、安卓用户授权补充。列入计划不等于已派工或已采集到数据。</div>
<div id="social-sources" style="margin-top:12px"></div></section>
<section class="card span12"><h2>Backlog / Blockers（待办 / 阻塞）</h2><div id="backlog"></div></section>
<section class="card span12"><h2>Product Contracts（产品合同）</h2><div id="contracts"></div></section>
<section class="card span12"><h2>Supervisor Tasks（真实任务）</h2><div class="small muted">只读 · Bridge 任务和进度实时同步 · 点击某个任务展开进度和产物</div><div id="task-list" style="margin-top:12px"></div><div id="task-detail" style="margin-top:12px"></div></section>
<section class="card span12"><h2>Recent Supervisor Runs（最近主管运行）</h2><div id="runs"></div></section>
<section class="card span12"><h2>Worker Changes & Handoffs（成员变化与任务交接）</h2>
<div class="small muted">自动检测上线/离线/能力变化；收到任务不等于通过验收，必须有可用成果才算交接成功。</div>
<h3>Current Handoffs（正在交接）</h3><div id="handoff-board"></div>
<h3>Latest Worker Changes（最近成员变更）</h3><div id="handoff-events"></div>
</section>
</div></main>
<script>
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct=s=>{const m=String(s).match(/(\d+)/);return m?Math.max(0,Math.min(100,+m[1])):0};
const cls=s=>/complete|healthy|available|accept/i.test(s)?'ok':/block|fail|unavailable|error/i.test(s)?'bad':'warn';
async function decideContract(id,approved){
 const verb=approved?'Approve and start（批准并启动）':'Reject（拒绝）';
 if(!confirm(`${verb} ${id}?`))return;
 const r=await fetch('/api/contracts/'+encodeURIComponent(id)+'/decision',{method:'POST',headers:{'Content-Type':'application/json','X-AI-Team-Action':'contract-decision'},body:JSON.stringify({approved})});
 const d=await r.json();
 if(!r.ok){alert('Action failed（操作失败）: '+(d.error||r.status));return;}
 await load();
}
function renderSocialSources(s){
 const root=document.getElementById('social-sources');
 let html='<div class="small muted">'+esc(s.name||'社交数据源扩展')+' · '+esc(s.status||'unknown')+'</div>';
 html+='<h3>Sources（数据源）</h3>';
 for(const p of s.platforms||[]){
  html+='<div class="row small"><b>'+esc(p.name)+' · '+esc(p.priority)+'</b><span class="warn">'+esc(p.status)+'</span></div>';
 }
 html+='<h3>Phases（阶段日程）</h3>';
 for(const phase of s.phases||[]){
  html+='<div class="row small"><div><b>'+esc(phase.id)+' · '+esc(phase.goal)+'</b><div class="muted">'+esc(phase.window)+'</div></div><span class="warn">'+esc(phase.status)+'</span></div>';
 }
 html+='<h3>Workers（责任安排；未派工不计进度）</h3>';
 for(const t of s.work_packages||[]){
  html+='<div class="row small"><div><code>'+esc(t.id)+'</code> · '+esc(t.owner)+'<div>'+esc(t.title)+'</div></div><span class="warn">'+esc(t.status)+'</span></div>';
 }
 root.innerHTML=html;
}
async function load(){const r=await fetch('/api/status',{cache:'no-store'});const d=await r.json();
 document.querySelector('#refresh').textContent='updated（已刷新） '+new Date().toLocaleTimeString();
 renderSocialSources(d.social_sources||{});
 document.querySelector('#program').innerHTML='<div class="muted">Weighted V1（加权总进度） <b>'+esc(d.program.weighted_progress)+'</b></div>'+d.program.milestones.map(m=>`<div class="row"><div><b>${esc(m.name)}</b><div class="bar"><i style="width:${pct(m.percent)}%"></i></div></div><div class="${cls(m.state)}">${esc(m.percent)} · ${esc(m.state)}</div></div>`).join('');
 document.querySelector('#agents').innerHTML=d.agents.map(a=>`<span class="agent"><i class="dot ${a.available?'on':''}"></i><b>${esc(a.agent_id)}</b><span class="muted">${a.available?'online（在线）':a.configured?'waiting（等待）':'off（关闭）'}</span></span>`).join('');
 const p=d.product;document.querySelector('#product').innerHTML=`<h3>${esc(p.name)}</h3><div class="row"><span>Overall（整体）</span><b>${esc(p.overall)}</b></div><div class="row"><span>Gate（当前关卡）</span><b>${esc(p.gate)}</b></div><div class="row"><span>Blocker（阻塞）</span><span class="warn">${esc(p.blocker)}</span></div>`+p.stages.map(x=>`<div class="row small"><span>${esc(x.stage)}</span><span class="${cls(x.state)}">${esc(x.progress)} · ${esc(x.state)}</span></div>`).join('');
 const r3=d.r3_readiness||{};document.querySelector('#r3').innerHTML=`<div class="row"><span>Preparation（准备）</span><b class="${r3.preparation_complete?'ok':'warn'}">${r3.preparation_complete?'ready（就绪）':'pending（待准备）'}</b></div><div class="row"><span>Real gates（真实关卡）</span><b class="${r3.r3_complete?'ok':'warn'}">${esc(r3.stage_progress||'unknown')}</b></div>`+(r3.gates||[]).map(g=>`<div class="row small"><div><b>${esc(g.label)}</b>${g.blocker?'<div class="muted">'+esc(g.blocker)+'</div>':''}</div><span class="${g.passed?'ok':'warn'}">${g.passed?'PASS（通过）':'PENDING（待完成）'}</span></div>`).join('');
 document.querySelector('#backlog').innerHTML=d.backlog.map(x=>`<div class="row"><div><code>${esc(x.id)}</code><div>${esc(x.item)}</div></div><b class="${cls(x.state)}">${esc(x.state)}</b></div>`).join('')||'<span class="muted">none（无）</span>';
 document.querySelector('#contracts').innerHTML=d.contracts.map(x=>`<div class="run"><div class="row"><div><code>${esc(x.contract_id)}</code><div><b>${esc(x.product_name)}</b></div><div class="small muted">${esc(x.idea)}</div></div><div class="${cls(x.status)}"><b>${esc(x.status)}</b></div></div><div class="small">Approval（批准）: ${x.approval_required?'required（需要）':x.approved?'approved（已批准）':'not required（不需要）'}${x.run_id?' · Run（运行）: '+esc(x.run_id):''}</div>${x.status==='draft'?`<div class="actions"><button onclick="decideContract('${esc(x.contract_id)}',true)">Approve（批准并启动）</button><button onclick="decideContract('${esc(x.contract_id)}',false)">Reject（拒绝）</button></div>`:''}</div>`).join('')||'<span class="muted">no contracts（暂无产品合同）</span>';
 document.querySelector('#runs').innerHTML=d.runs.map(x=>`<div class="run"><div class="row"><div><code>${esc(x.run_id)}</code><div>${esc(x.goal)}</div></div><div class="${cls(x.status)}"><b>${esc(x.overall_percent)}%</b> · ${esc(x.status)} / ${esc(x.phase)}</div></div><div class="small"><b>Schedule（日程）:</b> <span class="${cls(x.schedule_state)}">${esc(x.schedule_state)}</span>${x.project_due_at?' · <b>Due（截止）:</b> '+esc(x.project_due_at):''}</div><div class="small"><b>Next（下一动作）:</b> ${esc(x.next_action)}</div>${x.blockers.length?'<div class="small bad"><b>Blockers（阻塞）:</b> '+esc(x.blockers.join(' | '))+'</div>':''}${x.artifacts.map(a=>`<div class="artifact small">artifact（成果）: ${esc(a)}</div>`).join('')}</div>`).join('')||'<span class="muted">no runs（暂无运行）</span>';
 renderHandoffs(d);
}
function renderHandoffs(state){
 const board=document.getElementById('handoff-board');
 const events=document.getElementById('handoff-events');
 board.replaceChildren();events.replaceChildren();
 const rows=(state.runs||[]).flatMap(run=>(run.handoffs?.latest||[]).map(h=>({...h,run_id:run.run_id})));
 if(!rows.length){
  const empty=document.createElement('div');empty.className='muted';
  empty.textContent='当前没有新增交接记录（旧任务不会被回填为已交接）';board.appendChild(empty);
 }
 for(const h of rows){
  const row=document.createElement('div');row.className='row';
  const left=document.createElement('div');
  left.textContent=String(h.from_worker||'?')+' → '+String(h.to_worker||'?')+
    ' · '+String(h.phase||'')+' · '+String(h.run_id||'');
  const right=document.createElement('b');right.className=cls(h.status);
  const names={assigned:'已分配，待接手',in_progress:'已启动，待验收',
    delivered:'交接文件已齐备（仍需项目验收）',failed:'接任失败，需再次分工'};
  right.textContent=names[h.status]||String(h.status||'unknown');
  row.appendChild(left);row.appendChild(right);board.appendChild(row);
 }
 if(!(state.worker_changes||[]).length){
  const empty=document.createElement('div');empty.className='muted';
  empty.textContent='尚未检测到新的成员状态变更';events.appendChild(empty);
 }
 for(const item of (state.worker_changes||[]).slice(0,12)){
  const row=document.createElement('div');row.className='row small';
  row.textContent=String(item.agent_id||'?')+' · '+String(item.kind||'?')+
    ' · '+String(item.detected_at||'');events.appendChild(row);
 }
}
load().catch(e=>document.querySelector('#refresh').textContent='error（错误） '+e);setInterval(()=>load().catch(()=>{}),5000);
</script>
<script>
// 中文说明：任务列表/详情只读，字段使用 textContent 而不是原始 HTML 插值，避免注入。
let selectedTaskId = null;
const taskList = document.getElementById('task-list');
const taskDetail = document.getElementById('task-detail');
const cell=(node,text,clazz)=>{
  const d=document.createElement('div'); if(clazz)d.className=clazz;
  d.textContent=String(text==null?'':text);node.appendChild(d);return d;
};
async function loadTasks(){
  const response=await fetch('/api/tasks',{cache:'no-store'});
  if(!response.ok)throw Error('HTTP '+response.status);
  const data=await response.json();taskList.replaceChildren();
  if(!data.available){cell(taskList,'Bridge 不可达：任务状态暂时无法核验','bad');return;}
  if(!data.items.length){cell(taskList,'暂无任务记录','muted');return;}
  for(const task of data.items){
    const line=cell(taskList,'','row');
    const left=cell(line,'');
    const btn=document.createElement('button');btn.style.cssText='background:#202737;border:1px solid #384963;border-radius:8px;color:#d5e4fc;padding:5px 8px;cursor:pointer';
    btn.textContent=(task.title||task.task_id||'任务')+' · '+(task.agent_id||'unknown');
    btn.onclick=()=>showTask(task.task_id);left.appendChild(btn);
    cell(left,task.task_id+' · '+(task.started?'已实际启动':'尚未启动'),'small muted');
    cell(line,task.status||'unknown',task.status==='completed'?'ok':task.status==='failed'?'bad':'warn');
  }
}
async function showTask(taskId){
  selectedTaskId=taskId;
  taskDetail.replaceChildren();
  cell(taskDetail,'读取任务进度…','muted');
  try {
    const response=await fetch('/api/tasks/'+encodeURIComponent(taskId),{cache:'no-store'});
    if(!response.ok)throw Error('HTTP '+response.status);
    const task=await response.json();
    if(selectedTaskId!==taskId)return;
    taskDetail.replaceChildren();
    const block=cell(taskDetail,'','card');
    cell(block,'任务：'+task.task_id+' · '+task.status+' · '+(task.started?'已启动':'未启动'));
    cell(block,'进度事件（最后 40 条）','small muted');
    if(!task.progress.length)cell(block,'没有 Worker 上报的进度事件（不推测百分比）','muted');
    for(const event of task.progress){
      const detail=[event.stage||'unknown',event.percent==null?'':String(event.percent)+'%',event.message||''].filter(Boolean).join(' · ');
      cell(block,detail,'row small');
    }
    cell(block,'产物（已登记）','small muted');
    if(!task.artifacts.length)cell(block,'没有已登记产物','muted');
    for(const a of task.artifacts){cell(block,(a.label||a.kind||'file')+'：'+(a.path||a.artifact_id||''),'row small artifact');}
  } catch(e) {taskDetail.replaceChildren();cell(taskDetail,'读取失败：'+e,'bad');}
}
// 中文说明：监控面板只使用 DOM textContent，不能把任务标题/日志拼成 innerHTML。
// 保留展开状态：页面每 7 秒刷新，不让用户正在看的子任务突然折叠。
const expandedMonitorRuns=new Set();
let monitorHasRendered=false;
const statusCN={completed:'完成',running:'执行中',claimed:'已领取',queued:'排队中',
  pending:'等待中',cancelled:'已取消',failed:'失败',blocked:'阻塞',not_started:'未开始'};
const monitorStatus=s=>statusCN[s]||String(s||'未知');
const makeProgress=(host,percent,note)=>{
  const row=cell(host,'','monitor-progress');
  if(typeof percent==='number' && Number.isFinite(percent)){
    const clamped=Math.max(0,Math.min(100,percent));
    const track=cell(row,'','monitor-track');
    const fill=cell(track,'','monitor-fill');fill.style.width=clamped+'%';
    cell(row,clamped+'%'+(note?' · '+note:''),'small muted');
  }else cell(row,'进度未上报'+(note?' · '+note:''),'small muted');
};
const taskStatusRow=(parent,t)=>{
  const line=cell(parent,'','monitor-task');
  const left=cell(line,'','monitor-task-info');
  const name=cell(left,'','');
  if(t.task_id){
    const btn=document.createElement('button');btn.className='monitor-link';
    btn.textContent=(t.role||t.title||t.task_id)+' · '+(t.worker_id||'未分配');
    btn.onclick=()=>{showTask(t.task_id);document.getElementById('task-detail').scrollIntoView({behavior:'smooth',block:'nearest'});};
    name.appendChild(btn);
  }else cell(name,t.role||t.title||'未派工');
  cell(left,String(t.task_id||'')+(t.source==='supervisor_snapshot'?' · Supervisor 记录':''),'small muted');
  if(t.progress?.stage)cell(left,t.progress.stage+(t.progress.message?' · '+t.progress.message:''),'small muted');
  const right=cell(line,'','monitor-task-status');
  cell(right,monitorStatus(t.status),/fail|block|cancel/i.test(t.status)?'bad':t.status==='completed'?'ok':'warn');
  makeProgress(right,t.progress?.percent);
};
async function loadMonitor(){
  const response=await fetch('/api/monitor',{cache:'no-store'});
  if(!response.ok)throw Error('HTTP '+response.status);
  const d=await response.json();
  const runsRoot=document.getElementById('monitor-runs');
  const workersRoot=document.getElementById('monitor-workers');
  // 刷新前读取用户实际展开状态，避免覆盖用户的点击。
  for(const details of runsRoot.querySelectorAll('details[data-run-id]')){
    if(details.open)expandedMonitorRuns.add(details.dataset.runId);
    else expandedMonitorRuns.delete(details.dataset.runId);
  }
  runsRoot.replaceChildren();workersRoot.replaceChildren();
  if(!d.bridge_available)cell(runsRoot,'Bridge 不可达；显示 Supervisor 最后保存的任务状态，实时 Worker 进度可能过期','bad');
  if(!(d.runs||[]).length)cell(runsRoot,'暂无项目级运行记录','muted');
  for(const r of d.runs||[]){
    const details=document.createElement('details');details.className='monitor-run';
    details.dataset.runId=r.run_id||'';
    details.open=expandedMonitorRuns.has(r.run_id)||(!monitorHasRendered && ['blocked','running','primary_running','review_running','validation_running','implementation_running'].includes(r.status));
    const summary=document.createElement('summary');
    const heading=cell(summary,'','monitor-summary-title');
    cell(heading,(r.goal||r.run_id||'项目').slice(0,150),'monitor-name');
    cell(heading,(r.run_id||'')+' · '+monitorStatus(r.status)+' · '+(r.task_count||0)+' 项子任务','small muted');
    const score=cell(summary,'','monitor-summary-progress');
    makeProgress(score,r.overall_percent,'流程进度');
    details.appendChild(summary);
    const body=cell(details,'','monitor-run-body');
    if(r.blockers?.length)cell(body,'阻塞原因：'+r.blockers.join(' · '),'small bad');
    if(r.next_action)cell(body,'下一步：'+r.next_action,'small muted');
    for(const phase of r.phases||[]){
      const phaseRoot=cell(body,'','monitor-phase');
      const header=cell(phaseRoot,'','monitor-phase-header');
      cell(header,phase.name+' · '+monitorStatus(phase.status)+' · '+phase.task_count+'项','monitor-name');
      makeProgress(header,phase.percent,'阶段流程');
      const phaseTasks=(r.tasks||[]).filter(t=>t.phase===phase.id);
      if(!phaseTasks.length)cell(phaseRoot,'尚未分配具体任务','small muted');
      for(const t of phaseTasks)taskStatusRow(phaseRoot,t);
    }
    runsRoot.appendChild(details);
  }
  if(!(d.workers||[]).length)cell(workersRoot,'暂无 Worker 数据','muted');
  const grid=cell(workersRoot,'','monitor-worker-grid');
  for(const w of d.workers||[]){
    const block=cell(grid,'','monitor-worker');
    const header=cell(block,'','monitor-worker-header');
    cell(header,w.agent_id||'未知','monitor-name');
    cell(header,w.online?(w.busy?'在线·有任务':'在线·暂无执行中任务'):'离线',w.online?'ok':'bad');
    cell(block,'当前活跃：'+w.active_count+' · 最近任务样本：'+w.sampled_task_count+' · 完成 '+w.sampled_completed+' · 失败 '+w.sampled_failed,'small muted');
    for(const task of w.tasks||[]){
      const t={
        ...task,worker_id:w.agent_id,role:task.title||task.task_id,
        source:'bridge',
      };
      taskStatusRow(block,t);
    }
    if(!(w.tasks||[]).length)cell(block,'暂无最近任务记录（不是完成率 0%）','small muted');
  }
  cell(workersRoot,d.task_sample_note||'','small muted');
  cell(workersRoot,d.progress_note||'','small muted');
  monitorHasRendered=true;
}
loadMonitor().catch(e=>cell(document.getElementById('monitor-runs'),'进度读取失败：'+e,'bad'));
setInterval(()=>loadMonitor().catch(()=>{}),7000);
loadTasks().catch(()=>cell(taskList,'任务数据暂不可用','bad'));
setInterval(()=>loadTasks().catch(()=>{}),7000);
</script></body></html>'''


def make_handler(
    bridge_url: str,
    *,
    contracts: ProductContractStore | None = None,
    team: TeamSupervisor | Any | None = None,
):
    # GET /health 和网页首次加载不应依赖外置 SSD 上的合同/任务状态库。
    contract_store = contracts
    # 只读面板启动不能等待 Supervisor/Bridge 的大量任务库读取。
    # Supervisor 仅在用户主动批准合同时才实例化，避免磁盘繁忙时阻塞监听端口。
    team_supervisor = team

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/" or self.path.startswith("/?"):
                self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/api/status":
                payload = json.dumps(build_state(bridge_url), ensure_ascii=False, indent=2).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
                return
            if self.path == "/api/tasks":
                payload = json.dumps(_recent_tasks(bridge_url), ensure_ascii=False).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
                return
            if self.path == "/api/monitor":
                try:
                    payload = json.dumps(_monitor_state(bridge_url), ensure_ascii=False).encode("utf-8")
                    self._send(200, payload, "application/json; charset=utf-8")
                except Exception:
                    self._send(503, b'{"error":"monitor_unavailable"}\n', "application/json")
                return
            task_match = re.fullmatch(r"/api/tasks/(task_[A-Za-z0-9_-]{1,80})", self.path)
            if task_match:
                try:
                    payload = json.dumps(_task_detail(bridge_url, task_match.group(1)), ensure_ascii=False).encode("utf-8")
                    self._send(200, payload, "application/json; charset=utf-8")
                except (KeyError, ValueError):
                    self._send(404, b'{"error":"task_not_found"}\n', "application/json")
                except Exception:
                    self._send(503, b'{"error":"bridge_unavailable"}\n', "application/json")
                return
            if self.path == "/health":
                self._send(200, b'{"status":"ok"}\n', "application/json")
                return
            self._send(404, b'{"error":"not_found"}\n', "application/json")

        def do_POST(self) -> None:  # noqa: N802
            match = re.fullmatch(r"/api/contracts/([^/]+)/decision", self.path)
            if not match:
                self._send(404, b'{"error":"not_found"}\n', "application/json")
                return
            # 中文说明：合同批准会启动真实模型任务，因此只接受 Dashboard 自己发出的 JSON 动作。
            # 自定义 Header 会让跨站 fetch 触发 CORS 预检；本服务不开放 CORS，从而降低误触风险。
            if self.headers.get("X-AI-Team-Action") != "contract-decision":
                self._send(403, b'{"error":"action_header_required"}\n', "application/json")
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._send(415, b'{"error":"application_json_required"}\n', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            if length <= 0 or length > 4096:
                self._send(400, b'{"error":"invalid_body_length"}\n', "application/json")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                approved = payload.get("approved")
                if approved is not True and approved is not False:
                    raise ValueError("approved must be boolean")
                contract_id = unquote(match.group(1))
                result = (contract_store or ProductContractStore(CONTRACT_DIR)).decide_and_start(
                    contract_id=contract_id,
                    approved=approved,
                    team=team_supervisor or TeamSupervisor(
                        BridgeClient(bridge_url), state_dir=RUN_STATE_DIR
                    ),
                )
                body = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except KeyError as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(404, body, "application/json; charset=utf-8")
            except (ValueError, json.JSONDecodeError) as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(400, body, "application/json; charset=utf-8")

        def log_message(self, fmt: str, *args: Any) -> None:
            # 中文说明：dashboard 默认保持安静，避免每 5 秒前端刷新污染运维日志。
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Team local project dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.bridge))
    print(json.dumps({"status":"listening","url":f"http://{args.host}:{args.port}/"}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
