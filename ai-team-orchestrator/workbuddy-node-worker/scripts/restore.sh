#!/usr/bin/env bash
# WorkBuddy Worker 恢复脚本（重启机器 / Bridge 重启后使用）
# 用法：bash scripts/restore.sh
#
# 作用：
#   1. 确认 Bridge 在线（不在线则尝试提示，不擅自改别人的服务）；
#   2. 停掉可能残留的旧 worker；
#   3. 重新启动并做一次就绪确认。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_URL="${AI_TEAM_BRIDGE_URL:-http://127.0.0.1:8765}"

echo "=== 1. 检查 Bridge ==="
if curl -s -o /dev/null --noproxy '*' --max-time 4 "$BRIDGE_URL/" 2>/dev/null; then
  echo "✓ Bridge 在线：$BRIDGE_URL"
else
  echo "✗ Bridge 不可达：$BRIDGE_URL"
  echo "  请先启动 AI Team Bridge，再执行本脚本。"
  exit 1
fi

echo ""
echo "=== 2. 停止旧 worker（若在运行）==="
bash "$HERE/stop.sh" || true

echo ""
echo "=== 3. 启动 worker ==="
bash "$HERE/start.sh"
START_CODE=$?

echo ""
echo "=== 4. 就绪确认 ==="
sleep 3
bash "$HERE/status.sh"

exit $START_CODE
