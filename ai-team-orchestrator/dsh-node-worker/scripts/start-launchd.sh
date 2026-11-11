#!/bin/bash
# DSH Worker 常驻启动入口：只使用 Bridge 凭据文件路径，不写入任何密钥内容。
# macOS 用户 LaunchAgent 调用；当 DSH 模型不可用时由 Worker 诚实上报离线。
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH="$(cd "$HERE/.." && pwd)"
NODE_BIN="${DSH_NODE_BIN:-$HOME/.local/opt/nodejs/v22.22.2/bin/node}"
DSH_BIN="${DSH_BIN:-$HOME/.dsh/profiles/node_modules/@deepseek-ai/dsh/lib/bin.js}"
TOKEN_FILE="${AI_TEAM_BOOTSTRAP_TOKEN_FILE:-$ORCH/.orchestrator/bootstrap-token}"

if [ ! -x "$NODE_BIN" ]; then echo 'DSH worker: Node runtime missing' >&2; exit 78; fi
if [ ! -f "$DSH_BIN" ]; then echo 'DSH worker: DSH entrypoint missing' >&2; exit 78; fi
if [ ! -s "$TOKEN_FILE" ]; then echo 'DSH worker: Bridge token file missing' >&2; exit 78; fi

export DSH_NODE_BIN="$NODE_BIN"
export DSH_BIN
export AI_TEAM_BRIDGE_URL="${AI_TEAM_BRIDGE_URL:-http://127.0.0.1:8765}"
export AI_TEAM_BOOTSTRAP_TOKEN_FILE="$TOKEN_FILE"
cd "$ORCH"
exec "$NODE_BIN" dsh-node-worker/src/index.js
