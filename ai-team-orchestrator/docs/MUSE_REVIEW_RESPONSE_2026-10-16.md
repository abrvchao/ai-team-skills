# AI Team / Radar — Muse 审查处理记录（2026-10-16）

审查依据：用户提交的 `ai-team-radar-review-20261016.md`，针对公开交接分支 `agent-handoff-20261015`。以下为本地离线修复及待办，不是产品交付验收。

| Muse 编号 | 处理状态 | 实际说明 |
|---|---|---|
| C1 | GitHub 快照缺件已补 | 本地 Mac 模块一直可导入，但先前推送遗漏了 `r3_seed_compare.py`、`r3_user_session.py`、`r3_validation.py`。本次纳入审查分支并增加干净快照验证；R3 真实用户验收仍未通过。 |
| C2 | 代码门禁修复，部署待验收 | SourceGrant 必须匹配外部受信 HMAC 授权记录，缺失、撤销、记录篡改时 fail-closed；尚无生产签发者与真实平台授权。 |
| C3 | 代码门禁修复，部署待验收 | ReviewApproval 必须匹配**独立审核密钥**签名记录，且与暂存证据指纹绑定；尚无生产审核流程与权限分离。 |
| H1 | 包装器离线修复 | `dsh_cli_wrapper.py` 增加可配置 1–3600 秒硬超时、同进程组终止，避免无限占用。DSH 模型门禁仍未恢复。 |
| H2 | 未修 | `persistence_allowed=False` 尚未在独立的生产写入入口强制执行；需要生产存储适配层拒绝暂存预览，不能把当前预览能力当作落库授权。 |
| H3 | 未修 | GSC `passed=true` 文件尚无独立可信的运行证据签名；真实 GSC 验证仍不能宣布通过。 |
| M1 | 未修 | `evidence_sha256` 尚未完整贯通 read model / API / Dashboard 的溯源展示。 |
| M2 | 包装器离线修复 | 畸形请求得到结构化 failed 状态，避免无状态 traceback。 |
| M3 | 包装器离线修复 | 模板只允许白名单占位符，未知字段拒绝并输出 failed。 |
| M4 | 包装器离线修复 | 子进程环境变量显式允许列表，不透传无关 API Key/Token。仍须审查插件自身日志和凭据管理。 |
| L1/L2 | 待安排 | 对外暴露时 Dashboard 鉴权以及人工标注真实性证据仍须补齐。 |

## 验证证据

- Mac 本地，2026-10-16：Radar 全量 `python3 -m unittest discover -p 'test_*.py' -q` → **353 tests OK**；此前 348 tests OK 是 Mac 工作区而非当时的 GitHub 不完整快照。
- DSH wrapper 合成离线测试：`python3 -m unittest test_dsh_cli_wrapper -v` → **5 tests OK**，包含实际本地超时终止。
- 两条授权门禁测试：`python3 -m unittest test_source_intake test_source_promotion test_android_staging -v` → **38 tests OK**。
- 禁止调用付费 Worker、Grok、火山、未获授权的第三方 API；上述测试没有做这些调用。

## 下一步交接

1. **P0** 创建生产源授权管理员与独立审核者职责、可信存储和轮换，记录真实第三方/设备授权；未完成前新门禁会默认拒绝。
2. **P0** 将 `RawEventPreview` 的不可落库约束变成生产写入入口的强制拒绝测试，防止调用者绕过 `.persistence_allowed`。
3. **P0** 补 GSC / 人工标注可信执行证据；实测 Radar Lite 浏览器完整操作（R3 仍未通过）。
4. **P1** 修复 Dashboard 证据指纹链路，明确工作区、翻译及数据源实际体验。
5. **P1** DSH 免费目录尚过期；不刷新第三方目录、不启动模型任务，直到用户明确授权且免费资格校验为 true。Grok 必须保持禁用。

请后续 Agent 先看 `content-opportunity-radar/docs/SOURCE_AUTHORITY_GATE_V1.md` 和相关测试，再提供可离线复现的 PR。**GitHub 仓库是审查快照，不能用它宣称 Mac 的整套服务已部署**。
