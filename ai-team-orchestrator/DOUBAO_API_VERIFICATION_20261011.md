# 火山方舟 / 豆包 Worker 实测与故障修复记录（2026-10-11）

## 本机真实执行结果

- 已复用账号管理的现有文件：~/.config/ai-team/doubao-api-key（文件权限 0600）。
- 当前模型：doubao-seed-2-1-pro-260628；API 入口：火山方舟北京区域。
- 真实 API 预检：HTTP 429，返回精确错误码 **SetLimitExceeded**。
- 单次诊断复核：仍为 HTTP 429 / SetLimitExceeded；没有得到模型正文。
- 官方含义：该账号对模型或接入点设置的用量上限已触发，服务暂时暂停。
- 未打印、复制、上传或修改 API Key；未尝试绕过或提高计费限制。

## 修复与验证

- doubao-node-worker/src/index.js：识别 SetLimitExceeded 为 quota_exceeded；不得在错误日志中打印服务端响应体。
- doubao-node-worker/src/readiness.js：credential_unavailable 和 quota_exceeded 不重试；其他模型临时错误仍可按退避策略处理。
- doubao-node-worker/test/doubao.test.js：新增额度错误码映射、脱敏和禁止重试测试。
- 测试实测：**11/11 通过**；JS 静态语法校验通过。
- 已通过 bootstrap token 向运行中的 Bridge 上报：doubao model_available=false，reason=quota_exceeded。
- 实测 Bridge：doubao 离线（quota_exceeded），dsh 在线，qwen 在线。
- 豆包 macOS 启动服务已预备好，但尚未加载，避免反复调用受限 API。

## 阻塞与后续

需要管理员在火山方舟控制台 **开通管理／模型用量上限（安心体验模式）**
或 **接入点详情／接入点用量上限** 核对触发的限制。
调整/关闭限制可能产生费用，必须先确认预算；本轮未做此类变更。
限制恢复后，再进行一次受控真实预检，成功才启动 Worker 和派工。

官方错误码：https://docs.volcengine.com/docs/ark/error-codes?lang=en

## 真实性说明

注册、凭据文件存在或离线原因上报，都不等于在线模型可用。
当前 **没有通过 DOUBAO_MODEL_OK** 的真实预检，因此豆包不能标记 available=true。
