#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATUS="$ROOT/docs/AI_TEAM_STATUS.md"
BACKLOG="$ROOT/docs/BACKLOG.md"
fail=0

pass(){ echo "PASS（通过） $*"; }
block(){ echo "BLOCKED（阻塞） $*"; fail=1; }

# 中文说明：毕业预检只读，不启动任何 Worker，也不修改设备或项目。
if grep -q '| M0 .*| 100% | complete |' "$STATUS"; then pass "M0 Reliable Core（可靠核心）"; else block "M0 未完成"; fi
if grep -q '| M1 .*| 100% | complete |' "$STATUS"; then pass "M1 Autonomous Product Loop（自主产品闭环）"; else block "M1 未完成"; fi
if grep -q '| M3 .*| 80% |' "$STATUS" || grep -q '| M3 .*| 100% |' "$STATUS"; then pass "M3 productization prep（产品化准备）"; else block "M3 产品化准备不足"; fi

if command -v adb >/dev/null 2>&1 && adb devices 2>/dev/null | tail -n +2 | grep -q '[[:space:]]device$'; then
  pass "Android real device（安卓真机）已授权连接"
else
  block "Android real device（安卓真机）未连接；M2 Gate（M2 关卡）不能关闭"
fi

ANDROID_E2E_STATE="$ROOT/ai-team-orchestrator/.orchestrator/android-e2e/state.json"
ANDROID_E2E_STATUS=""
if [[ -f "$ANDROID_E2E_STATE" ]]; then
  ANDROID_E2E_STATUS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$ANDROID_E2E_STATE" 2>/dev/null || true)"
fi

if grep -q 'M2-ANDROID-001.*complete' "$BACKLOG"; then
  pass "M2 Android E2E（安卓端到端）已归档"
elif [[ "$ANDROID_E2E_STATUS" == "passed" ]]; then
  block "Android E2E evidence（安卓端到端证据）已通过，但 M2 状态文档尚未归档；下一次控制面刷新即可关闭关卡"
else
  block "M2-ANDROID-001 尚未完成"
fi

if [[ "$fail" == "0" ]] && grep -q 'M3-GRAD-001.*complete' "$BACKLOG"; then
  pass "M3-GRAD-001 V1 Final Graduation（V1 最终毕业）已归档"
  echo "GRADUATED（已毕业）: AI Team V1 graduation evidence（毕业证据）已满足并归档"
elif [[ "$fail" == "0" ]]; then
  echo "READY（就绪）: 可以进入 V1 Final Graduation（V1 最终毕业）"
else
  echo "NOT_READY（未就绪）: 先解决上方阻塞；不要提前宣称 V1 毕业"
fi
exit "$fail"
