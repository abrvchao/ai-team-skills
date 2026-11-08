# Doubao Node Worker

Doubao joins AI Team as a registered pull worker. Readiness calls the official Volcano Ark OpenAI-compatible API directly; task execution uses ByteDance's Trae Agent CLI so Doubao has real local file/shell tools.

Defaults:
- model: `doubao-seed-2-1-pro-260628`
- base URL: `https://ark.cn-beijing.volces.com/api/v3`
- Trae provider: `doubao`
- max steps: `60`

Credentials are never passed on argv or committed to YAML. Supported sources: `DOUBAO_API_KEY`, `ARK_API_KEY`, or `DOUBAO_API_KEY_FILE`.

Environment overrides: `TRAE_BIN`, `DOUBAO_MODEL`, `DOUBAO_BASE_URL`, `ARK_BASE_URL`, `DOUBAO_MAX_STEPS`, `DOUBAO_TRAE_CONFIG`, and `DOUBAO_TASK_TIMEOUT_MS`.

The worker requires an exact `DOUBAO_MODEL_OK` API preflight before registration. Trae step output is mapped to model/tool Progress Events, and requested artifacts are validated before completion.
