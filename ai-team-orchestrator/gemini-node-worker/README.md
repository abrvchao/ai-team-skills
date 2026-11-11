# Gemini Node Worker

Gemini joins AI Team as a real registered pull worker. It reuses the same
`AgentWorker` contract as DSH; the Bridge remains the source of truth for
queued/acknowledged/running/completed state.

## Prerequisites

1. Install the official Gemini CLI.
2. Authenticate Gemini CLI once on the local machine. For this safe AI Team route,
   the selected authentication method must be **Google account OAuth**
   (`oauth-personal`); an API Key or Vertex AI is not permitted.
3. Start the Bridge with Gemini explicitly enabled:

```bash
python bridge.py \
  --workspace-root /path/to/workspaces \
  --pull-agent dsh \
  --pull-agent gemini
```

4. Point the worker at the Bridge bootstrap token:

```bash
export AI_TEAM_BOOTSTRAP_TOKEN_FILE=/path/to/.orchestrator/bootstrap-token
export AI_TEAM_BRIDGE_URL=http://127.0.0.1:8765
npm start
```

The worker performs **one** real model preflight before registration. If it
fails, Gemini is not registered and therefore cannot become `available=true`.
It does **not** retry preflight every 30 seconds or switch to an API Key.

## Google AI Pro 与费用安全（2026-10）

**Pro 订阅 ≠ 按量计费的 Gemini API Key。** 本项目不读取
`~/.gemini/ai-team-api-key`，会对所有真实请求检查本机
`~/.gemini/settings.json` 选中的 `oauth-personal`，并要求显式环境许可
`AI_TEAM_GEMINI_OAUTH_APPROVED=1`。传给子进程的环境剔除 `GEMINI_API_KEY`、
`GOOGLE_API_KEY`、Vertex AI 相关变量；找不到有效的本地 OAuth refresh
凭据时直接拒绝，不改成另一个收费渠道。默认 `--approval-mode plan`
（只读），若之后批准编辑任务才考虑 `auto_edit`。

**注意 Google 官方文档存在矛盾：** [Gemini CLI 认证指南](https://geminicli.com/docs/get-started/authentication/)
仍提到 Google AI Pro/Ultra Google 账号登录；但
[Google 个人版 Code Assist 停用公告](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)
称自 2026-06-18 不再向个人订阅提供 Gemini CLI 服务。
**不能仅凭 Pro 购买凭据就声称 CLI 已可使用**，需要真实模型预检成功。

2026-10-18 Mac 本机情况：Gemini CLI `0.63.0`，OAuth 本地配置存在，
一次性安全预检返回 `cli_failure`（**未取得精确上游失败原因**），
尝试时 OAuth token 成功刷新，但模型没有返回 `GEMINI_MODEL_OK`。
没有加载 Gemini Worker、也没有调用 API Key；Bridge 仍应是
`gemini.available=false`。对该失败**不得自动重试**。

### 恢复部署前的要求

先确认 OAuth 登录的 Google 账号与用户购买 Pro 的账号一致，且当前
Google 服务端允许 Gemini CLI 使用这一账号；必要时重新登录或选择
Google 官方支持的订阅路由。未经用户新授权，不要走 AI Studio 按量计费
或 Vertex AI，也不要建立无限重试的开机自启动服务。

## Gemini CLI execution

The worker uses official headless mode:

```bash
gemini --prompt "..." --output-format stream-json --approval-mode plan
```

Environment options:

- `GEMINI_BIN`: executable path (default `gemini`)
- `GEMINI_MODEL`: optional model
- `GEMINI_APPROVAL_MODE`: default `plan`（只读）; `yolo` is never enabled by default
- `GEMINI_SANDBOX=1`: enable Gemini sandbox
- `GEMINI_SKIP_TRUST=1`: explicitly bypass folder trust
- `GEMINI_TASK_TIMEOUT_MS`: task timeout

When the shared Node Worker SDK supports progress, Gemini maps stream-json
events into structured progress stages. Artifact paths remain workspace-relative
and are validated by the Bridge.
