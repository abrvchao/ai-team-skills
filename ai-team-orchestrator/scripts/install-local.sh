#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run（演练）] %q ' "$@"; printf '\n'
  else
    "$@"
  fi
}

echo "AI Team local installer（AI Team 本地安装器）"
echo "root（根目录）: $ROOT"

# 中文说明：只创建仓库自己的虚拟环境，不修改系统 Python。
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  run "$PYTHON_BIN" -m venv "$ROOT/.venv"
fi

# 中文说明：MCP 依赖统一从 requirements-mcp.txt 安装，避免手工环境漂移。
# 中文说明：优先使用完整锁文件安装已验证的 Python 依赖树，避免 MCP SDK 或传递依赖漂移。
if [[ -f "$ROOT/requirements-mcp.lock.txt" ]]; then
  run "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements-mcp.lock.txt"
else
  run "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements-mcp.txt"
fi

# 中文说明：Node Worker 必须存在 package-lock.json，安装时统一使用 npm ci，保证依赖解析可重复。
for dir in "$ROOT"/node-worker "$ROOT"/*-node-worker; do
  [[ -f "$dir/package.json" ]] || continue
  if ! command -v npm >/dev/null 2>&1; then
    echo "FAIL（失败）: npm missing（未安装），无法安装 $dir" >&2
    exit 1
  fi
  if [[ ! -f "$dir/package-lock.json" ]]; then
    echo "FAIL（失败）: missing package-lock.json（缺少依赖锁文件） -> $dir" >&2
    exit 1
  fi
  run npm --prefix "$dir" ci --ignore-scripts --no-audit --no-fund
done

run "$ROOT/scripts/verify-install.sh"
echo "install complete（安装完成）"
