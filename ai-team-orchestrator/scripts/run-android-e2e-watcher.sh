#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin${PATH:+:$PATH}"
# 中文说明：优先使用显式配置，其次自动发现 Python，避免绑定某台 Mac 的用户名路径。
PYTHON_BIN="${AI_TEAM_PYTHON:-$(command -v python3.12 || command -v python3 || true)}"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.12 or python3 not found; set AI_TEAM_PYTHON." >&2
  exit 127
fi
# 中文说明：ADB 同样优先从 PATH 查找，Homebrew 路径仅作为 macOS 兜底。
ADB_BIN="${ADB_BIN:-$(command -v adb || true)}"
if [[ -z "$ADB_BIN" && -x /opt/homebrew/bin/adb ]]; then
  ADB_BIN="/opt/homebrew/bin/adb"
fi
if [[ -z "$ADB_BIN" ]]; then
  echo "adb not found; install Android platform-tools or set ADB_BIN." >&2
  exit 127
fi
cd "$ROOT"
# 中文说明：长期等待真机；只执行只读设备检查，不点击、不输入、不启动 App。
exec "$PYTHON_BIN" android_e2e_watcher.py \
  --bridge http://127.0.0.1:8765 \
  --state .orchestrator/android-e2e/state.json \
  --workspace .android-e2e-smoke \
  --adb "$ADB_BIN" \
  --interval 5
