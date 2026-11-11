"""独立的离线授权证据门禁。

运行时只读两个受信登记文件及独立 HMAC 密钥。调用方提供的 dataclass 本身
不能证明授权。登记文件与签名密钥均由外部授权管理员/审查者创建，绝不在此签发。
若未配置、被撤销、签名无效或磁盘数据不匹配则默认拒绝。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest, new
import json
import os
from pathlib import Path
import stat


class AuthorityError(ValueError):
    """不泄露密钥和敏感文件路径的授权失败。"""


_CONFIG = {
    "grant": ("RADAR_TRUSTED_GRANTS_FILE", "RADAR_GRANTS_VERIFY_KEY_FILE"),
    "review": ("RADAR_TRUSTED_REVIEWS_FILE", "RADAR_REVIEWS_VERIFY_KEY_FILE"),
}


def canonical_payload(record: object) -> dict:
    """稳定记录内容，精确比较所有字段；无宽松子集匹配。"""
    fields = asdict(record)
    for key, value in fields.items():
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise AuthorityError("untrusted_authorization")
            fields[key] = value.astimezone(timezone.utc).isoformat()
    return {**fields, "revoked": False}


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def verify_signed_record(kind: str, record: object) -> None:
    """使用受保护密钥核验独立登记项；同 reference 多份记录直接拒收。"""
    try:
        data_env, key_env = _CONFIG[kind]
        registry_path = os.environ[data_env]
        key_path = Path(os.environ[key_env])
        # 密钥必须独立存放，并且不允许其他本机用户读取。
        mode = key_path.stat().st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
            raise AuthorityError("untrusted_authorization")
        key = key_path.read_bytes()
        if len(key) < 32:
            raise AuthorityError("untrusted_authorization")
        doc = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        if doc.get("schema") != "radar-authority-v1" or doc.get("kind") != kind:
            raise AuthorityError("untrusted_authorization")
        payload = canonical_payload(record)
        ref_key = "grant_ref" if kind == "grant" else "review_ref"
        rows = [row for row in doc["records"] if isinstance(row, dict)
                and isinstance(row.get("payload"), dict)
                and row["payload"].get(ref_key) == payload[ref_key]]
        if len(rows) != 1 or rows[0]["payload"] != payload:
            raise AuthorityError("untrusted_authorization")
        signed = _canonical({"schema": "radar-authority-v1", "kind": kind,
                             "payload": payload})
        expected = new(key, signed, sha256).hexdigest()
        if not isinstance(rows[0].get("signature"), str) or not compare_digest(
                rows[0]["signature"], expected):
            raise AuthorityError("untrusted_authorization")
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise AuthorityError("untrusted_authorization") from None


def require_grant(grant: object) -> None:
    verify_signed_record("grant", grant)


def require_review(review: object) -> None:
    verify_signed_record("review", review)
