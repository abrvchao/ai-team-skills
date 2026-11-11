"""仅用于离线测试的合成授权签发器；生产代码没有签发密钥的创建接口。"""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from hmac import new
import json
import os
from pathlib import Path
import secrets
import tempfile
from unittest.mock import patch


def _claim(record):
    payload = asdict(record)
    for key, value in payload.items():
        if isinstance(value, datetime):
            payload[key] = value.astimezone(timezone.utc).isoformat()
    return {**payload, "revoked": False}


class SyntheticAuthority:
    """生成隔离、临时且只在测试进程内使用的两个签名域。"""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name)
        # 每次测试随机生成，避免把任何持久签名凭据提交 Git。
        self.keys = {"grant": secrets.token_bytes(32),
                     "review": secrets.token_bytes(32)}
        self.records = {"grant": {}, "review": {}}
        for kind, key in self.keys.items():
            key_file = self.path / f"{kind}.key"
            key_file.write_bytes(key)
            key_file.chmod(0o600)
            self._flush(kind)
        values = {
            "RADAR_TRUSTED_GRANTS_FILE": str(self.path / "grant.json"),
            "RADAR_GRANTS_VERIFY_KEY_FILE": str(self.path / "grant.key"),
            "RADAR_TRUSTED_REVIEWS_FILE": str(self.path / "review.json"),
            "RADAR_REVIEWS_VERIFY_KEY_FILE": str(self.path / "review.key"),
        }
        self.patcher = patch.dict(os.environ, values)
        self.patcher.start()

    def _flush(self, kind):
        data = {"schema": "radar-authority-v1", "kind": kind,
                "records": list(self.records[kind].values())}
        (self.path / f"{kind}.json").write_text(
            json.dumps(data, sort_keys=True), encoding="utf-8")

    def issue(self, kind, record):
        payload = _claim(record)
        body = {"schema": "radar-authority-v1", "kind": kind, "payload": payload}
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        signed = {"payload": payload,
                  "signature": new(self.keys[kind], canonical, sha256).hexdigest()}
        ref = payload["grant_ref" if kind == "grant" else "review_ref"]
        self.records[kind][str(ref)] = signed
        self._flush(kind)

    def grant(self, record):
        self.issue("grant", record)

    def review(self, record):
        self.issue("review", record)

    def close(self):
        self.patcher.stop()
        self.dir.cleanup()
