"""Portable AI Team Bridge client（可随主持 skill 携带的本机客户端）."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url: str):
        parts = urlsplit(base_url)
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1"
                or not parts.port or parts.username or parts.password
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError("bridge_must_be_loopback_http_with_port")
        self.base_url = base_url.rstrip("/")
        # 本机请求不经过系统代理，也不跟随可能把任务正文转发到外网的重定向。
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = Request(self.base_url + path, data=data, method=method,
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            # 上游错误正文可能包含任务/凭据，不直接回显。
            raise RuntimeError(f"bridge_http_{exc.code}") from None
        except (URLError, TimeoutError, OSError):
            suffix = "; write outcome unknown: inspect tasks before retry" if method == "POST" else ""
            raise RuntimeError("bridge_unreachable" + suffix) from None


def task_path(task_id: str) -> str:
    if not re.fullmatch(r"task_[A-Za-z0-9_-]{1,100}", task_id):
        raise ValueError("invalid_task_id")
    return "/tasks/" + task_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AI Team 主持操作入口（不调用模型）")
    parser.add_argument("--bridge", default=os.getenv("AGENT_BRIDGE_URL", "http://127.0.0.1:8765"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "agents", "tasks"):
        commands.add_parser(name)
    submit = commands.add_parser("submit")
    submit.add_argument("--file", type=Path, required=True)
    for name in ("task", "progress", "artifacts", "message", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("task_id")
        if name == "message":
            command.add_argument("--file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        client = Client(args.bridge)
        name = args.command
        if name in {"doctor", "agents", "tasks"}:
            result = client.request("GET", {"doctor": "/health", "agents": "/agents", "tasks": "/tasks"}[name])
        elif name == "submit":
            body = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(body, dict) or set(body) - {"agent_id", "title", "prompt", "workspace"}:
                raise ValueError("task_requires_only_agent_id_title_prompt_workspace")
            if not all(isinstance(body.get(key), str) and body[key].strip()
                       for key in ("agent_id", "title", "prompt", "workspace")):
                raise ValueError("task_fields_must_be_nonempty_strings")
            result = client.request("POST", "/tasks", body)
        else:
            path = task_path(args.task_id)
            if name in {"message", "cancel"}:
                body = {"message": args.file.read_text(encoding="utf-8")} if name == "message" else {}
                result = client.request("POST", path + ("/messages" if name == "message" else "/cancel"), body)
            else:
                result = client.request("GET", path + ("" if name == "task" else "/" + name))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        # 本地解析错误不包含文件正文；调用方可以据退出码停止后续派发。
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
