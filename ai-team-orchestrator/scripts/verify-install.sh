#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

check_cmd() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS（通过） $label"
  else
    echo "FAIL（失败） $label"
    fail=1
  fi
}

check_file() {
  local label="$1" path="$2"
  if [[ -f "$path" ]]; then echo "PASS（通过） $label"; else echo "FAIL（失败） $label -> $path"; fail=1; fi
}

echo "AI Team install doctor（安装环境体检）"
check_cmd "Python 3（Python 3）" python3 --version
check_cmd "Node.js（Node 运行时）" node --version
check_cmd "npm（Node 包管理器）" npm --version
check_file "requirements-mcp.txt（MCP 顶层依赖清单）" "$ROOT/requirements-mcp.txt"
check_file "requirements-mcp.lock.txt（MCP 完整依赖锁文件）" "$ROOT/requirements-mcp.lock.txt"
check_file "Team Supervisor（团队主管）" "$ROOT/team_supervisor.py"
check_file "Project Intake（项目接入）" "$ROOT/product_intake.py"
check_file "Project Dashboard（项目面板）" "$ROOT/project_dashboard.py"

# 中文说明：可重复安装依赖 lockfile；任一 Node Worker 缺锁文件都视为安装完整性失败。
for dir in "$ROOT"/node-worker "$ROOT"/*-node-worker; do
  [[ -f "$dir/package.json" ]] || continue
  check_file "$(basename "$dir") package-lock（依赖锁文件）" "$dir/package-lock.json"
done

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  echo "PASS（通过） Python venv（Python 虚拟环境）"
  if "$ROOT/.venv/bin/python" -c 'import mcp' >/dev/null 2>&1; then
    echo "PASS（通过） MCP SDK（MCP 开发包）"
  else
    echo "FAIL（失败） MCP SDK（MCP 开发包）"
    fail=1
  fi
else
  echo "WARN（警告） Python venv（Python 虚拟环境）尚未创建"
fi

if command -v adb >/dev/null 2>&1; then
  echo "PASS（通过） ADB（安卓调试桥）已安装"
  if adb devices 2>/dev/null | tail -n +2 | grep -q '[[:space:]]device$'; then
    echo "PASS（通过） Android device（安卓设备）已授权连接"
  else
    echo "BLOCKED（阻塞） Android device（安卓设备）未连接；这不会让安装验证失败"
  fi
else
  echo "WARN（警告） ADB（安卓调试桥）未安装；仅影响 Android Worker（安卓执行器）"
fi

exit "$fail"
