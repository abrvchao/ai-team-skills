"""Local human labeling UI（本地人工标注界面） for Radar R3 validation.

This server is intentionally local-only and never calls an AI model. Human clicks are
the evidence source for R3 relevance labels.
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from r3_validation import render_markdown, score_batch


DEFAULT_BATCH = Path(".radar/r3/human-label-pack/batch.json")
ACTION_HEADER = "X-Radar-Human-Action"
ACTION_VALUE = "set-label"
JUDGMENT_TO_RELEVANT = {
    "reliable": True,
    "uncertain": None,
    "unreliable": False,
}
RELEVANCE_LEVEL_TO_SCORE = {
    "strong": 1.0,
    "medium": 2 / 3,
    "weak": 1 / 3,
    "unrelated": 0.0,
    "uncertain": None,
}
RELEVANCE_LEVEL_TO_BINARY = {
    "strong": True,
    "medium": True,
    "weak": None,
    "unrelated": False,
    "uncertain": None,
}
LEGACY_JUDGMENT_TO_LEVEL = {
    "reliable": "strong",
    "uncertain": "uncertain",
    "unreliable": "unrelated",
}
LEVEL_TO_LEGACY_JUDGMENT = {
    "strong": "reliable",
    "medium": "reliable",
    "weak": "uncertain",
    "unrelated": "unreliable",
    "uncertain": "uncertain",
}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    # 中文说明：人工标签属于产品验收证据，必须原子写入，不能冒险留下半份 JSON。
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_batch(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("R3 batch must be a JSON object")
    # score_batch 同时充当 schema contract（结构合同）验证器；坏数据直接失败，不静默修补。
    score_batch(value)
    return value


def state_payload(path: Path) -> dict[str, Any]:
    batch = _load_batch(path)
    result = score_batch(batch)
    return {
        "batch": batch,
        "result": result,
        "human_only": True,
        "ai_autofill_allowed": False,
    }


def set_label(
    batch: dict[str, Any],
    *,
    scope: str,
    run_id: str,
    rank: int,
    relevant: bool,
    note: str = "",
) -> dict[str, Any]:
    if relevant is not True and relevant is not False:
        raise ValueError("relevant must be boolean")
    return set_judgment(
        batch,
        scope=scope,
        run_id=run_id,
        rank=rank,
        judgment="reliable" if relevant is True else "unreliable",
        note=note,
    )


def set_judgment(
    batch: dict[str, Any],
    *,
    scope: str,
    run_id: str,
    rank: int,
    judgment: str,
    note: str = "",
) -> dict[str, Any]:
    clean_note = " ".join(str(note or "").split()).strip()
    if len(clean_note) > 500:
        raise ValueError("note cannot exceed 500 characters")
    if judgment not in JUDGMENT_TO_RELEVANT:
        raise ValueError("judgment must be reliable, uncertain, or unreliable")
    if rank < 1 or rank > 100:
        raise ValueError("invalid rank")

    for queue in batch.get("queues") or []:
        document = queue.get("document") or {}
        if document.get("scope") != scope or document.get("run_id") != run_id:
            continue
        for item in document.get("items") or []:
            if int(item.get("rank") or 0) != rank:
                continue
            # 中文说明：旧版“靠谱/不靠谱”只能证明二元方向，不能事后伪造为“强相关/不相关”的分级证据。
            item["human_judgment"] = judgment
            item["relevant"] = JUDGMENT_TO_RELEVANT[judgment]
            item.pop("relevance_level", None)
            item.pop("relevance_score", None)
            item["note"] = clean_note
            return item
    raise KeyError(f"label target not found: {scope}/{run_id}/rank-{rank}")


def set_relevance_level(
    batch: dict[str, Any],
    *,
    scope: str,
    run_id: str,
    rank: int,
    relevance_level: str,
    note: str = "",
) -> dict[str, Any]:
    clean_note = " ".join(str(note or "").split()).strip()
    if len(clean_note) > 500:
        raise ValueError("note cannot exceed 500 characters")
    if relevance_level not in RELEVANCE_LEVEL_TO_SCORE:
        raise ValueError("relevance_level must be strong, medium, weak, unrelated, or uncertain")
    if rank < 1 or rank > 100:
        raise ValueError("invalid rank")

    for queue in batch.get("queues") or []:
        document = queue.get("document") or {}
        if document.get("scope") != scope or document.get("run_id") != run_id:
            continue
        for item in document.get("items") or []:
            if int(item.get("rank") or 0) != rank:
                continue
            # 中文说明：相关性强弱和“是否确定”不是一回事。新字段保留分级强度；
            # 旧 relevant 只服务冻结的 binary precision（二元精度）门槛，避免中途换规则。
            item["relevance_level"] = relevance_level
            item["relevance_score"] = RELEVANCE_LEVEL_TO_SCORE[relevance_level]
            item["human_judgment"] = LEVEL_TO_LEGACY_JUDGMENT[relevance_level]
            item["relevant"] = RELEVANCE_LEVEL_TO_BINARY[relevance_level]
            item["note"] = clean_note
            return item
    raise KeyError(f"label target not found: {scope}/{run_id}/rank-{rank}")


def _write_derived(batch_path: Path, batch: dict[str, Any]) -> dict[str, Any]:
    result = score_batch(batch)
    result_path = batch_path.parent / "result.json"
    report_path = batch_path.parent / "R3_VALIDATION_REPORT.md"
    _atomic_json(result_path, result)
    report_path.write_text(render_markdown(result), encoding="utf-8")
    return result


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radar R3 Human Labeling（Radar R3 人工标注）</title>
<style>
:root{color-scheme:dark;--bg:#0b0d12;--panel:#141923;--line:#293142;--text:#eef3fa;--muted:#9aa8ba;--ok:#65d58a;--bad:#ff7676;--accent:#79a8ff;--warn:#f4c35f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"PingFang SC",sans-serif}main{width:min(1200px,calc(100% - 28px));margin:24px auto 60px}h1{margin:0 0 4px}.muted{color:var(--muted)}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:18px 0}.card,.item{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}.queue{margin-top:22px}.item{margin:9px 0}.topic{font-size:16px;font-weight:700}.meta{font-size:12px;color:var(--muted);margin:4px 0 10px}.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}button{border:1px solid var(--line);background:#202737;color:var(--text);padding:8px 11px;border-radius:8px;cursor:pointer}button.strong.on{border-color:var(--ok);color:var(--ok)}button.medium.on{border-color:var(--accent);color:var(--accent)}button.weak.on{border-color:var(--warn);color:var(--warn)}button.unrelated.on{border-color:var(--bad);color:var(--bad)}button.uncertain.on{border-color:var(--muted);color:var(--muted)}input{flex:1;min-width:220px;background:#0f131b;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:8px}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}@media(max-width:800px){.summary{grid-template-columns:1fr 1fr}}
</style></head><body><main>
<h1>Radar R3 Human Labeling（Radar R3 人工标注）</h1>
<div class="muted">Human-only evidence（仅真人证据）· 强相关 / 中相关 / 弱相关 / 不相关 = 相关性强度 · 无法判断 = 暂不计分；旧 binary precision（二元精度）门槛保持冻结</div>
<div id="summary" class="summary"></div><div id="queues"></div>
<script>
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){const r=await fetch('/api/state',{cache:'no-store'});const d=await r.json();const x=d.result;
document.querySelector('#summary').innerHTML=`<div class="card">Binary Labels（二元标签）<br><b>${x.total_labeled_opportunities}/20</b></div><div class="card">Unique（去重主题）<br><b>${x.unique_labeled_topics}/20</b></div><div class="card">Micro P@5（微平均前五精度）<br><b>${x.micro_precision_at_k??'pending（待定）'}</b></div><div class="card">Graded Relevance（分级相关性）<br><b>${x.graded_mean_relevance_at_k??'pending（待定）'}</b></div><div class="card">Gate（关卡）<br><b class="${x.passed?'ok':'warn'}">${x.passed?'PASS（通过）':'PENDING（待完成）'}</b></div>`;
document.querySelector('#queues').innerHTML=d.batch.queues.map(q=>{const doc=q.document;return `<section class="queue"><h2>${esc(doc.scope)}</h2>${doc.items.map(i=>{const legacy=i.human_judgment??(i.relevant===true?'reliable':i.relevant===false?'unreliable':null);const level=i.relevance_level??(legacy==='reliable'?'strong':legacy==='unreliable'?'unrelated':legacy==='uncertain'?'uncertain':null);return `<div class="item"><div class="topic">#${i.rank} ${esc(i.topic)}</div><div class="meta">${esc(i.topic_id)} · providers（数据源）: ${esc((i.providers||[]).join(', '))}</div><div class="actions"><button class="strong ${level==='strong'?'on':''}" onclick="save('${esc(doc.scope)}','${esc(doc.run_id)}',${i.rank},'strong')">强相关</button><button class="medium ${level==='medium'?'on':''}" onclick="save('${esc(doc.scope)}','${esc(doc.run_id)}',${i.rank},'medium')">中相关</button><button class="weak ${level==='weak'?'on':''}" onclick="save('${esc(doc.scope)}','${esc(doc.run_id)}',${i.rank},'weak')">弱相关</button><button class="unrelated ${level==='unrelated'?'on':''}" onclick="save('${esc(doc.scope)}','${esc(doc.run_id)}',${i.rank},'unrelated')">不相关</button><button class="uncertain ${level==='uncertain'?'on':''}" onclick="save('${esc(doc.scope)}','${esc(doc.run_id)}',${i.rank},'uncertain')">无法判断</button><input id="note-${esc(doc.run_id)}-${i.rank}" value="${esc(i.note||'')}" placeholder="note（备注，可选）"></div></div>`}).join('')}</section>`}).join('');}
async function save(scope,runId,rank,relevanceLevel){const note=document.getElementById(`note-${runId}-${rank}`).value;const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json','X-Radar-Human-Action':'set-label'},body:JSON.stringify({scope,run_id:runId,rank,relevance_level:relevanceLevel,note})});const d=await r.json();if(!r.ok){alert('Save failed（保存失败）: '+(d.error||r.status));return;}await load();}
load().catch(e=>document.querySelector('#queues').textContent='Error（错误）: '+e);
</script></main></body></html>'''


def make_handler(batch_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == "/api/state":
                try:
                    body = json.dumps(state_payload(batch_path), ensure_ascii=False).encode("utf-8")
                    self._send(200, body)
                except Exception as exc:
                    self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                return
            self._send(404, b'{"error":"not_found"}')

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/label":
                self._send(404, b'{"error":"not_found"}')
                return
            # 中文说明：人工标签会改变验收证据，所以要求同源 UI 的自定义动作头；服务不开放 CORS。
            if self.headers.get(ACTION_HEADER) != ACTION_VALUE:
                self._send(403, b'{"error":"human_action_header_required"}')
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._send(415, b'{"error":"application_json_required"}')
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            if length <= 0 or length > 16384:
                self._send(400, b'{"error":"invalid_body_length"}')
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                batch = _load_batch(batch_path)
                relevance_level = body.get("relevance_level")
                judgment = body.get("judgment")
                if relevance_level is not None:
                    item = set_relevance_level(
                        batch,
                        scope=str(body.get("scope") or ""),
                        run_id=str(body.get("run_id") or ""),
                        rank=int(body.get("rank") or 0),
                        relevance_level=str(relevance_level),
                        note=str(body.get("note") or ""),
                    )
                elif judgment is not None:
                    item = set_judgment(
                        batch,
                        scope=str(body.get("scope") or ""),
                        run_id=str(body.get("run_id") or ""),
                        rank=int(body.get("rank") or 0),
                        judgment=str(judgment),
                        note=str(body.get("note") or ""),
                    )
                else:
                    # 中文说明：继续接受旧版 relevant=true/false 请求，避免已有本地流程突然失效。
                    item = set_label(
                        batch,
                        scope=str(body.get("scope") or ""),
                        run_id=str(body.get("run_id") or ""),
                        rank=int(body.get("rank") or 0),
                        relevant=body.get("relevant"),
                        note=str(body.get("note") or ""),
                    )
                _atomic_json(batch_path, batch)
                result = _write_derived(batch_path, batch)
                payload = json.dumps({"saved": True, "item": item, "result": result}, ensure_ascii=False).encode("utf-8")
                self._send(200, payload)
            except KeyError as exc:
                self._send(404, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"))

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar local human labeling UI")
    parser.add_argument("--batch", default=str(DEFAULT_BATCH))
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    batch_path = Path(args.batch)
    _load_batch(batch_path)
    # 中文说明：刻意固定绑定 127.0.0.1，不提供公网 host 参数，人工验收界面默认只能本机访问。
    server = ThreadingHTTPServer(("127.0.0.1", max(1, min(args.port, 65535))), make_handler(batch_path))
    print(json.dumps({"status":"listening","url":f"http://127.0.0.1:{server.server_address[1]}/","human_only":True}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
