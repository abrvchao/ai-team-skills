#!/usr/bin/env bash
# WorkBuddy Worker 启动脚本
# 用法：bash scripts/start.sh
# 说明：在后台拉起 workbuddy-node-worker，pid/日志写入 workbuddy-node-worker/.run/。
#      凭据只通过「文件路径」传递，绝不写进命令行或日志。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCH="$(cd "$HERE/.." && pwd)"
RUN_DIR="$HERE/.run"
PID_FILE="$RUN_DIR/worker.pid"
LOG_FILE="$RUN_DIR/worker.log"
mkdir -p "$RUN_DIR"

# --- 解析 Node：优先官方独立安装，不再依赖 WorkBuddy 的 Node 路径。---
resolve_node() {
  if [ -n "${WORKBUDDY_NODE_BIN:-}" ]; then echo "$WORKBUDDY_NODE_BIN"; return; fi
  local own_node="$HOME/.local/opt/nodejs/v22.22.2/bin/node"
  if [ -x "$own_node" ]; then echo "$own_node"; return; fi
  local cur="$HOME/.workbuddy/binaries/node/versions/current"
  if [ -f "$cur" ]; then
    local v; v="$(tr -d '[:space:]' < "$cur")"
    local p="$HOME/.workbuddy/binaries/node/versions/$v/bin/node"
    if [ -x "$p" ]; then echo "$p"; return; fi
  fi
  command -v node
}

NODE_BIN="$(resolve_node)"
: "${NODE_BIN:?找不到 node 可执行文件，请设置 WORKBUDDY_NODE_BIN}"

# 独立 CLI 带完整交互 TUI bundle；桌面 App 附带的 CLI 缺 dist/codebuddy.js。
STANDALONE_CLI="$HOME/.local/opt/codebuddy-cli/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy"
CLI_BIN="${WORKBUDDY_CLI:-$STANDALONE_CLI}"
if [ ! -f "$CLI_BIN" ]; then
  echo "✗ 找不到独立 CodeBuddy CLI：$CLI_BIN" >&2
  echo "  请先安装官方 @tencent-ai/codebuddy-code，或设置 WORKBUDDY_CLI。" >&2
  exit 1
fi

BRIDGE_URL="${AI_TEAM_BRIDGE_URL:-http://127.0.0.1:8765}"
TOKEN_FILE="${AI_TEAM_BOOTSTRAP_TOKEN_FILE:-$ORCH/.orchestrator/bootstrap-token}"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "✗ 找不到 bootstrap token 文件：$TOKEN_FILE" >&2
  echo "  请先启动 AI Team Bridge（它会生成该文件）。" >&2
  exit 1
fi

# 已在运行则直接返回
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✓ WorkBuddy worker 已在运行 (pid=$(cat "$PID_FILE"))"
  exit 0
fi

echo "· node        : $NODE_BIN"
echo "· codebuddy   : $CLI_BIN"
echo "· bridge      : $BRIDGE_URL"
echo "· token file  : $TOKEN_FILE"
echo "· log         : $LOG_FILE"

cd "$HERE"
AI_TEAM_BRIDGE_URL="$BRIDGE_URL" \
AI_TEAM_BOOTSTRAP_TOKEN_FILE="$TOKEN_FILE" \
WORKBUDDY_NODE_BIN="$NODE_BIN" \
WORKBUDDY_CLI="$CLI_BIN" \
nohup "$NODE_BIN" src/index.js >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
sleep 2

if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✓ WorkBuddy worker 已启动 (pid=$(cat "$PID_FILE"))"
else
  echo "✗ WorkBuddy worker 启动后立即退出，请查看日志：$LOG_FILE" >&2
  tail -n 30 "$LOG_FILE" >&2 || true
  exit 1
fi
