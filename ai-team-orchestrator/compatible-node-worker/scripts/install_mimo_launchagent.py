"""安装 MiMo 用户级 Worker：密钥保存在独立 0600 文件，环境仅传文件路径。"""
import os
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
LABEL = "ai.team.mimo"
KEY = ROOT / ".orchestrator" / "model-keys" / "mimo.key"
TOKEN = ROOT / ".orchestrator" / "bootstrap-token"

def main():
    if not KEY.is_file() or not TOKEN.is_file():
        raise SystemExit("MiMo key or Bridge bootstrap token missing")
    if KEY.stat().st_mode & 0o077:
        raise SystemExit("MiMo key permissions are not private")
    log_dir = ROOT / ".orchestrator" / "worker-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "RunAtLoad": True,
        # 中文说明：先关闭自动重试，防止鉴权故障造成反复产生付费请求。
        "KeepAlive": False,
        "ProgramArguments": ["/opt/homebrew/bin/node", str(ROOT / "compatible-node-worker/src/index.js")],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "MODEL_WORKER_ID": "mimo",
            "MODEL_API_KEY_FILE": str(KEY),
            "AI_TEAM_BOOTSTRAP_TOKEN_FILE": str(TOKEN),
            "AI_TEAM_BRIDGE_URL": "http://127.0.0.1:8765",
        },
        "StandardOutPath": str(log_dir / "mimo.stdout.log"),
        "StandardErrorPath": str(log_dir / "mimo.stderr.log"),
    }
    directory = HOME / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / (LABEL + ".plist")
    output.write_bytes(plistlib.dumps(payload))
    os.chmod(output, 0o600)
    print("MiMo LaunchAgent installed (not yet started)")

if __name__ == "__main__":
    main()
