#!/usr/bin/env bash
# WorkBuddy Worker 停止脚本
# 用法：bash scripts/stop.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$HERE/.run/worker.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "· 没有 pid 文件，worker 未在运行"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "· 进程 $PID 已不存在，清理 pid 文件"
  rm -f "$PID_FILE"
  exit 0
fi

echo "· 正在停止 worker (pid=$PID) ..."
kill "$PID" 2>/dev/null || true

for _ in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "✓ 已停止"
    exit 0
  fi
  sleep 0.5
done

echo "· 优雅退出超时，强制结束"
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "✓ 已强制停止"
