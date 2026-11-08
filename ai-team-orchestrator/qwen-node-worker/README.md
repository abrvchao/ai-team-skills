# Qwen Node Worker

Qwen joins AI Team as a registered pull worker backed by the official Qwen Code CLI.

Runtime defaults:
- CLI: `qwen`
- model: `qwen3-coder-plus`
- base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- approval: `auto`
- output: `stream-json`

Credentials are never passed on argv. Supported sources: `QWEN_API_KEY`, `DASHSCOPE_API_KEY`, `BAILIAN_CODING_PLAN_API_KEY`, `BAILIAN_TOKEN_PLAN_API_KEY`, or `QWEN_API_KEY_FILE`.

Environment overrides: `QWEN_BIN`, `QWEN_MODEL`, `QWEN_BASE_URL`, `QWEN_APPROVAL_MODE`, `QWEN_MAX_WALL_TIME`, and `QWEN_TASK_TIMEOUT_MS`.

The worker performs a real `QWEN_MODEL_OK` preflight before registration and maps Qwen Code stream-json session/tool events into Orchestrator Progress Events.
