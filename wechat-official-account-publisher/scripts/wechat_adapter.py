#!/usr/bin/env python3
"""Create a WeChat Official Account draft after explicit authorization."""
import argparse, json, os, sys
from pathlib import Path
from urllib import parse, request
from validate_bundle import validate

def request_json(url, method="GET", payload=None, timeout=20):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"} if body else {}, method=method)
    with request.urlopen(req, timeout=timeout) as response: return json.loads(response.read().decode("utf-8"))

def build_payload(article, html):
    item = {"title": article["title"], "author": article.get("author", ""), "digest": article["digest"], "content": html, "thumb_media_id": article["cover_media_id"], "need_open_comment": int(bool(article.get("need_open_comment", False))), "only_fans_can_comment": int(bool(article.get("only_fans_can_comment", False)))}
    if article.get("content_source_url"): item["content_source_url"] = article["content_source_url"]
    return {"articles": [item]}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("bundle_dir", type=Path); action = parser.add_mutually_exclusive_group(required=True); action.add_argument("--dry-run", action="store_true"); action.add_argument("--submit", action="store_true"); args = parser.parse_args()
    errors = validate(args.bundle_dir, require_media=True)
    if errors:
        for error in errors: print(f"ERROR: {error}", file=sys.stderr)
        return 1
    article = json.loads((args.bundle_dir / "article.json").read_text(encoding="utf-8")); html = (args.bundle_dir / "content.html").read_text(encoding="utf-8"); payload = build_payload(article, html)
    if args.dry_run:
        print(json.dumps({"status": "dry-run", "article_count": len(payload["articles"]), "title": article["title"]}, ensure_ascii=False)); return 0
    app_id, secret = os.environ.get("WECHAT_APP_ID"), os.environ.get("WECHAT_APP_SECRET")
    if not app_id or not secret: print("ERROR: WECHAT_APP_ID and WECHAT_APP_SECRET are required", file=sys.stderr); return 2
    timeout = int(os.environ.get("WECHAT_TIMEOUT_SECONDS", "20")); token_url = os.environ.get("WECHAT_TOKEN_URL", "https://api.weixin.qq.com/cgi-bin/token")
    token_data = request_json(f"{token_url}?{parse.urlencode({'grant_type':'client_credential','appid':app_id,'secret':secret})}", timeout=timeout); token = token_data.get("access_token")
    if not token:
        print(json.dumps({"status":"error","stage":"token","errcode":token_data.get("errcode"),"errmsg":token_data.get("errmsg")}, ensure_ascii=False), file=sys.stderr); return 3
    draft_url = os.environ.get("WECHAT_DRAFT_ADD_URL", "https://api.weixin.qq.com/cgi-bin/draft/add")
    result = request_json(f"{draft_url}?{parse.urlencode({'access_token':token})}", method="POST", payload=payload, timeout=timeout); media_id = result.get("media_id")
    print(json.dumps({"status":"created" if media_id else "error","media_id":media_id,"errcode":result.get("errcode"),"errmsg":result.get("errmsg")}, ensure_ascii=False)); return 0 if media_id else 4

if __name__ == "__main__": raise SystemExit(main())
