"""Radar content translation layer（Radar 内容翻译层）.

展示层翻译，不修改 Radar 原始证据、评分或 Handoff 数据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request


LANGUAGE_NAMES = {
    "zh": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
}

DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_KEY_FILE = Path("~/.config/ai-team/qwen-api-key").expanduser()
DEFAULT_CACHE = Path(__file__).resolve().parent / ".radar" / "translation-cache.sqlite3"
# 重要：API Key 文件存在不是用户授权计费。默认禁用按量计费翻译调用。
PAID_API_APPROVAL_ENV = "RADAR_TRANSLATION_PAID_API_APPROVED"


def _clean_json_content(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = re.sub(r"^.{3}(?:json)?\s*", "", text, count=1, flags=re.I)
        if text.endswith(fence):
            text = text[:-3].rstrip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("translation provider returned non-object JSON")
    return payload


class TranslationService:
    """Small cached translation adapter backed by the existing Qwen account."""

    def __init__(
        self,
        *,
        cache_path: str | Path = DEFAULT_CACHE,
        model: str | None = None,
        base_url: str | None = None,
        api_key_file: str | Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.cache_path = Path(cache_path).expanduser().resolve()
        self.model = str(
            model
            or os.environ.get("RADAR_TRANSLATION_MODEL")
            or os.environ.get("QWEN_MODEL")
            or DEFAULT_MODEL
        )
        self.base_url = str(
            base_url
            or os.environ.get("RADAR_TRANSLATION_BASE_URL")
            or os.environ.get("QWEN_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        configured_key_file = api_key_file or os.environ.get("QWEN_API_KEY_FILE")
        self.api_key_file = (
            Path(configured_key_file).expanduser()
            if configured_key_file
            else DEFAULT_KEY_FILE
        )
        self.timeout = timeout
        self._initialize_cache()

    def _initialize_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache_path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    cache_key TEXT PRIMARY KEY,
                    target_language TEXT NOT NULL,
                    model TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _api_key(self) -> str:
        for name in ("RADAR_TRANSLATION_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY"):
            value = str(os.environ.get(name) or "").strip()
            if value:
                return value
        try:
            value = self.api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("translation credential is not configured") from exc
        if not value:
            raise RuntimeError("translation credential is empty")
        return value

    def _cache_key(self, target_language: str, text: str) -> str:
        raw = (self.model + "\x00" + target_language + "\x00" + text).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _cached(
        self,
        target_language: str,
        texts: list[str],
    ) -> tuple[dict[str, str], list[str]]:
        found: dict[str, str] = {}
        missing: list[str] = []
        with sqlite3.connect(self.cache_path) as db:
            for text in texts:
                key = self._cache_key(target_language, text)
                row = db.execute(
                    "SELECT translated_text FROM translations WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if row:
                    found[text] = str(row[0])
                else:
                    missing.append(text)
        return found, missing

    def _store(
        self,
        target_language: str,
        pairs: list[tuple[str, str]],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.cache_path) as db:
            db.executemany(
                """
                INSERT OR REPLACE INTO translations
                    (cache_key, target_language, model, source_text, translated_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        self._cache_key(target_language, source),
                        target_language,
                        self.model,
                        source,
                        translated,
                        now,
                    )
                    for source, translated in pairs
                ],
            )

    def _call_provider(
        self,
        *,
        target_language: str,
        texts: list[str],
    ) -> list[str]:
        # 每次非缓存调用都重新核验显式付费许可；不能因为有 Qwen key 就自动扣费。
        if os.environ.get(PAID_API_APPROVAL_ENV) != "1":
            raise ValueError(
                "paid_translation_not_approved: live translation requires "
                "explicit RADAR_TRANSLATION_PAID_API_APPROVED=1"
            )
        prompt = {
            "task": "Translate the following product content.",
            "target_language": LANGUAGE_NAMES[target_language],
            "rules": [
                "Preserve meaning, tone, proper nouns, product names and technical terms.",
                "Do not translate URLs, IDs, citations, code, model names or provider names.",
                "Do not add explanations or facts.",
                "Return strict JSON only with one translations array in exactly the same order and length.",
            ],
            "texts": texts,
        }
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a precise translation engine. Output strict JSON only.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = request.Request(
            self.base_url + "/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + self._api_key(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        provider_content = payload["choices"][0]["message"]["content"]
        parsed = _clean_json_content(provider_content)
        translations = parsed.get("translations")
        if not isinstance(translations, list) or len(translations) != len(texts):
            raise RuntimeError(
                "translation provider returned an invalid translations array"
            )
        return [
            str(item).strip() or source
            for item, source in zip(translations, texts)
        ]

    def translate(
        self,
        *,
        target_language: str,
        texts: list[str],
    ) -> dict[str, Any]:
        target = str(target_language or "").lower().split("-", 1)[0]
        if target not in LANGUAGE_NAMES:
            raise ValueError("target_language is not supported")
        if not isinstance(texts, list):
            raise ValueError("texts must be an array")
        if len(texts) > 40:
            raise ValueError("texts may contain at most 40 items")

        cleaned: list[str] = []
        for item in texts:
            text = str(item or "").strip()
            if len(text) > 1600:
                raise ValueError(
                    "each translation item must be 1600 characters or fewer"
                )
            cleaned.append(text)
        if sum(len(item) for item in cleaned) > 16000:
            raise ValueError("translation request is too large")

        unique = list(dict.fromkeys(item for item in cleaned if item))
        cached, missing = self._cached(target, unique)
        if missing:
            translated = self._call_provider(
                target_language=target,
                texts=missing,
            )
            pairs = list(zip(missing, translated))
            self._store(target, pairs)
            cached.update(pairs)

        return {
            "status": "completed",
            "provider": "qwen",
            "model": self.model,
            "target_language": target,
            "cache_hits": len(unique) - len(missing),
            "translated_count": len(missing),
            "translations": [
                cached.get(item, item) if item else ""
                for item in cleaned
            ],
        }
