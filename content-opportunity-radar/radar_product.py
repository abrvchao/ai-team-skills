"""Radar Lite local product（Radar 精简版本地产品）."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from api import serve
from radar_lite import handoff_by_rank, render_handoff_markdown, run_lite_scan
from read_model import OpportunityReadStore
from translation import TranslationService
from tracker import DEFAULT_CRON
from tracker_runtime import RadarTrackerRuntime
from workspace import WorkspaceStore


PRODUCT_VERSION = "radar-lite-product-v0.1"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = PROJECT_ROOT / ".radar" / "radar.db"
DEFAULT_HANDOFF_ROOT = PROJECT_ROOT / ".radar" / "lite" / "handoffs"
LANGUAGE_COUNTRY_DEFAULTS = {
    "zh": "CN",
    "en": "US",
    "ja": "JP",
    "ko": "KR",
    "fr": "FR",
    "de": "DE",
    "es": "ES",
    "pt": "BR",
}


def _text(value: Any, *, name: str, maximum: int, default: str | None = None) -> str:
    text = str(value if value is not None else default or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > maximum:
        raise ValueError(f"{name} is too long")
    return text


def _optional_text(value: Any, *, name: str, maximum: int) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _text(value, name=name, maximum=maximum)


def _bounded_int(value: Any, *, name: str, minimum: int, maximum: int, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _language(value: Any) -> str:
    language = _text(value, name="language", maximum=12, default="en")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", language):
        raise ValueError("language must be a short language code such as zh, en, ja, or ko")
    return language.lower()


def _country(value: Any, *, language: str) -> str:
    default = LANGUAGE_COUNTRY_DEFAULTS.get(language.split("-", 1)[0], "US")
    country = _text(value, name="country", maximum=2, default=default).upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country must be a two-letter code such as CN, US, JP, or KR")
    return country


def _slug(value: str, *, fallback: str = "opportunity") -> str:
    # 中文说明：路径完全由服务端生成，客户端不能传输出路径。
    safe = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return safe[:64] or fallback


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class RadarProductActions:
    def __init__(
        self,
        *,
        database: str | Path = DEFAULT_DB,
        handoff_root: str | Path = DEFAULT_HANDOFF_ROOT,
        translation_service: TranslationService | None = None,
    ) -> None:
        self.database = str(Path(database).expanduser().resolve())
        self.handoff_root = Path(handoff_root).expanduser().resolve()
        self.translation_service = translation_service
        self.tracker_runtime = RadarTrackerRuntime(self.database)

    def run_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = _text(payload.get("scope"), name="scope", maximum=80, default="AI Agents")
        top = _bounded_int(payload.get("top"), name="top", minimum=1, maximum=5, default=5)
        language = _language(payload.get("language"))
        country = _country(payload.get("country"), language=language)
        workspace_id = _optional_text(payload.get("workspace_id"), name="workspace_id", maximum=128)
        result = run_lite_scan(
            scope=scope,
            db=self.database,
            top=top,
            language=language,
            country=country,
            workspace_id=workspace_id,
        )
        return {
            "status": "completed",
            "product_version": PRODUCT_VERSION,
            "scope": scope,
            "language": language,
            "country": country,
            "run_id": result.get("run_id"),
            "cards": result.get("cards") or [],
        }

    def create_handoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = _text(payload.get("scope"), name="scope", maximum=80, default="AI Agents")
        rank = _bounded_int(payload.get("rank"), name="rank", minimum=1, maximum=10, default=1)
        workspace_id = _optional_text(payload.get("workspace_id"), name="workspace_id", maximum=128)
        run_id = _optional_text(payload.get("run_id"), name="run_id", maximum=128)
        with OpportunityReadStore(self.database, read_only=True, initialize=False) as store:
            handoff = handoff_by_rank(
                store,
                scope=scope,
                rank=rank,
                workspace_id=workspace_id,
                run_id=run_id,
            )
        source = handoff.get("source") or {}
        topic = _text(source.get("topic"), name="handoff source topic", maximum=160)
        resolved_run = str(source.get("radar_run_id") or run_id or "latest")
        folder = self.handoff_root / _slug(resolved_run, fallback="latest") / (
            f"rank-{rank:02d}-{_slug(topic)}"
        )
        json_path = folder / "HANDOFF.json"
        md_path = folder / "HANDOFF.md"
        _atomic_text(json_path, json.dumps(handoff, ensure_ascii=False, indent=2) + "\n")
        _atomic_text(md_path, render_handoff_markdown(handoff))
        return {
            "status": "created",
            "product_version": PRODUCT_VERSION,
            "topic": topic,
            "rank": rank,
            "run_id": source.get("radar_run_id"),
            "handoff_path": str(json_path),
            "markdown_path": str(md_path),
        }

    def translate(self, payload: dict[str, Any]) -> dict[str, Any]:
        language = _language(payload.get("target_language"))
        texts = payload.get("texts")
        if not isinstance(texts, list):
            raise ValueError("texts must be an array")
        if self.translation_service is None:
            # 中文说明：翻译服务惰性创建，普通扫描/查看不会触发凭据或缓存初始化。
            self.translation_service = TranslationService()
        return self.translation_service.translate(
            target_language=language,
            texts=texts,
        )

    def create_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        # 中文说明：工作区仅保存配置，不迁移或修改任何历史扫描记录。
        with WorkspaceStore(self.database) as store:
            result = store.create(
                name=_text(payload.get("name"), name="name", maximum=120),
                scope=_text(payload.get("scope"), name="scope", maximum=160),
                language=_language(payload.get("language")),
                country=_country(payload.get("country"), language=_language(payload.get("language"))),
            )
            return result.to_dict()

    def update_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _text(payload.get("workspace_id"), name="workspace_id", maximum=128)
        options: dict[str, Any] = {}
        for key in ("name", "scope", "language", "country"):
            if key in payload:
                options[key] = payload[key]
        if "language" in options:
            options["language"] = _language(options["language"])
        if "country" in options:
            options["country"] = _country(options["country"], language=options.get("language", "en"))
        with WorkspaceStore(self.database) as store:
            try:
                return store.update(workspace_id, **options).to_dict()
            except KeyError as exc:
                raise ValueError("workspace not found") from exc

    def start_background_scheduler(self) -> None:
        self.tracker_runtime.start_background_scheduler()

    def stop_background_scheduler(self) -> None:
        self.tracker_runtime.stop_background_scheduler()

    def tracker_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _text(
            payload.get("workspace_id"),
            name="workspace_id",
            maximum=128,
        )
        cron_expr = _text(
            payload.get("cron_expr"),
            name="cron_expr",
            maximum=100,
            default=DEFAULT_CRON,
        )
        schedule = self.tracker_runtime.create_schedule(
            workspace_id,
            cron_expr=cron_expr,
        )
        return {"status": "scheduled", "schedule": schedule}

    def tracker_run_now(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _text(
            payload.get("workspace_id"),
            name="workspace_id",
            maximum=128,
        )
        return self.tracker_runtime.run_now(workspace_id)

    def tracker_tick(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"items": self.tracker_runtime.tick()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Content Opportunity Radar Lite local product（内容机会雷达精简产品）"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--handoff-root", default=str(DEFAULT_HANDOFF_ROOT))
    args = parser.parse_args()
    actions = RadarProductActions(database=args.db, handoff_root=args.handoff_root)
    print(
        json.dumps(
            {
                "product": "Content Opportunity Radar Lite",
                "version": PRODUCT_VERSION,
                "url": f"http://{args.host}:{max(1, min(args.port, 65535))}",
                "mode": "native-lite",
                "actions": ["scan", "handoff", "translate", "tracker"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    serve(
        database=actions.database,
        host=args.host,
        port=max(1, min(args.port, 65535)),
        actions=actions,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
