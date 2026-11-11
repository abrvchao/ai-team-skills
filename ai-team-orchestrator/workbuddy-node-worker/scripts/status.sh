#!/usr/bin/env bash
# WorkBuddy Worker 状态检查
# 用法：bash scripts/status.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$HERE/.run/worker.pid"
LOG_FILE="$HERE/.run/worker.log"
BRIDGE_URL="${AI_TEAM_BRIDGE_URL:-http://127.0.0.1:8765}"

echo "=== WorkBuddy Worker 状态 ==="

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "进程      : ✓ 运行中 (pid=$(cat "$PID_FILE"))"
else
  echo "进程      : ✗ 未运行"
fi

echo -n "Bridge    : "
if curl -s -o /dev/null --noproxy '*' --max-time 4 "$BRIDGE_URL/" 2>/dev/null; then
  echo "✓ $BRIDGE_URL 可达"
else
  echo "✗ $BRIDGE_URL 不可达"
fi

echo ""
echo "--- Bridge 视角的 agent 列表 ---"
curl -s --noproxy '*' --max-time 5 "$BRIDGE_URL/agents" 2>/dev/null | head -c 1200 || echo "(该端点不存在或未实现)"

echo ""
echo ""
echo "--- 最近 15 行日志 ---"
if [ -f "$LOG_FILE" ]; then
  tail -n 15 "$LOG_FILE"
else
  echo "(暂无日志)"
fi
