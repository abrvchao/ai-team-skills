#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

remove_path() {
  local path="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run（演练）] remove（删除） $path"
  else
    rm -rf "$path"
  fi
}

# 中文说明：卸载只清理由安装器创建的可重建运行环境；绝不删除项目、任务成果、账号或密钥。
remove_path "$ROOT/.venv"
for dir in "$ROOT"/node-worker "$ROOT"/*-node-worker; do
  [[ -d "$dir/node_modules" ]] && remove_path "$dir/node_modules"
done

echo "uninstall complete（卸载完成；项目和密钥均保留）"
