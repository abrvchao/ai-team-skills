# Radar 五平台采集入口契约（草稿）

> 阶段：`P0` 离线安全门禁。该分支从 GitHub `main` 创建；Mac 上的较新改动与安卓 Worker **未同步**，不得直接合并部署。用户的本机项目计划见 `ai-team-orchestrator/tasks/china-social-source-expansion-20261012.json`（目前仅在 Mac 工作区，不在本分支）。

## 目标与边界

本次只实现 **staged_only（暂存）**数据校验层。采集尚未接入真实 API、手机或 Radar 生产库，不能把五个平台的支持名单解读为已经获得平台的搜索权限。后续经代码审查并取得正式授权后，才增加平台 adapter 和到 `core.RawEvent` 的转换。

- 可选平台：知乎、抖音、B站、小红书、闲鱼。
- 来源类型：`official_api`（官方 API）、`oauth_api`（授权 API）、`licensed`（许可数据）、`android_screen`（用户授权的可见屏幕）。
- 输入白名单：`platform, source_id, title, captured_at, canonical_url, metrics`。未知字段一律拒收，防止完整 HTTP 响应、账号凭据、手机设备标识等误存。
- 授权采用显式 `SourceGrant`，必须有效、未到期且范围与平台一致。API 路径还要求 `verified_free` 或明确 `user_approved_paid` + `user_approved_charge=True`。本模块只校验传入的授权证据，**不负责证明授权真实性或发出付费请求**。
- HTTPS 公共资源 URL 必须属于对应平台域名；移除 URL 查询串、fragment、嵌入式认证信息。
- 暂存输出包含 `dedupe_key`（来源、路径、平台维度的稳定去重键）、`evidence_sha256`（证据哈希）、`grant_ref`（授权记录引用）与 `status=staged_only`。不存原始敏感响应。
- 暂存内容不得被直接当作 `core.RawEvent`，尤其 `android_screen` 不能冒充 `AcquisitionMethod.HTML`。

## 与既有 Radar 代码的关系

GitHub 当前的 `core.py` 已有 `RawEvent`、`Provenance` 和 `AcquisitionMethod`；`social.py` 已有 `SocialObservation`、跨平台去重与指标到 Signal 的转换。**不修改现有评分算法**。`source_intake.py` 提供原始证据入库前的单独权限/隐私关口，后续 Adapter 将获批准的 `staged_only` 事件映射到已有 `RawEvent`，并保留置信度、获取方式和许可边界。

## 离线验收

运行：

```bash
cd content-opportunity-radar
python -m unittest -v test_source_intake
python -m unittest discover -v -p 'test_*.py'
```

11 项离线用例覆盖五平台白名单、过期授权、未知费用、显式付费许可、安卓无授权拒收、未经许可的多余字段、URL 脱敏、稳定去重、非法指标、时间戳与跨平台授权串用。**只有 CI 真正完成才填写通过记录**。

## 后续任务（不在本 PR 声称完成）

1. GitHub 草稿 PR 与本机工作区双向对齐，不能拿旧 `main` 覆盖 Mac 最新代码。
2. MiMo 查平台官方 API 是否真实可申请、配额/商业授权；DSH 研究经用户授权的安卓只读 UI 证据。分别提交可复核报告。
3. Qwen 连接经审批的采集 adapter → 暂存门禁 → Radar `RawEvent` 的最小闭环；OpenCode 独立审查权限、成本与日志脱敏。
4. 用户亲自授权安卓设备后才进行 P2 真机验证；不能绕过验证码、平台访问控制、反爬机制，也不能采集私人聊天及私密账号信息。

## Docker / Bridge

2026-10-12 晚本机 Tunnel Client 报告超过 300 秒未在线，本 PR 只在 GitHub 上推进。**本机 Bridge、Dashboard、Worker 的新运行状态尚无法核实**，不据此推断 Docker 数据迁移导致故障。恢复连接后先验证 Tunnel、Bridge 健康、任务去重与外置盘 I/O，再合并或派工。