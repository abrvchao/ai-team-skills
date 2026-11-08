"""安装 Radar macOS 用户级常驻服务（不会修改其他 Agent 的配置）。"""

from __future__ import annotations

import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.kakanb.radar-lite"
TARGET = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def main() -> None:
    logs = ROOT / ".radar" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    # 中文说明：使用固定绝对路径；绑定回环地址，避免直接暴露 Radar API。
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            "/opt/homebrew/bin/python3",
            str(ROOT / "radar_product.py"),
            "--host", "127.0.0.1",
            "--port", "8787",
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "launchd.stdout.log"),
        "StandardErrorPath": str(logs / "launchd.stderr.log"),
    }
    TARGET.write_bytes(plistlib.dumps(payload))
    print(f"LaunchAgent written: {TARGET}")


if __name__ == "__main__":
    main()
