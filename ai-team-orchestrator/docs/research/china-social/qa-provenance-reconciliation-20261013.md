# Radar 来源证据与 Dashboard 对账（2026-10-13）

此文档仅为**独立离线核查记录**，不是 SOC-QA-001 Worker 交付，也不代表真实平台 API 已授权。

## 已核验

- 本机 Bridge 8765 的 `/health`、`/tasks`，旧面板 8770 `/health`，新版 8772 `/health`、`/api/status` 均返回 HTTP 200。
- 计划文件与 8772 `/api/status` 均包含五个平台、P0–P3 四阶段和四项工作包，四项状态逐一相同。
- SOC-API-001：`documented_only_no_live_access`；MiMo 原任务失败，只有独立文档成果，没有线上接口授权。
- SOC-ANDROID-001：`implementation_design_verified_device_blocked`；DSH 报告已完成，P2 真机授权仍未完成。
- SOC-SCHEMA-001 和 SOC-QA-001：均 `scheduled_not_dispatched`，不得虚报完成。
- Radar 全量离线回归 339/339，数据入口、审核、安卓摘要专项 25/25；Dashboard Python 9/9、Node 8/8。

## 新增代码边界

- Radar `android_staging.py` 只接收经独立授权、裁剪脱敏及人工复核的屏幕摘要，不访问设备、不存原图、不转生产 RawEvent。
- `source_promotion.py` 补齐异常时钟和非法授权路径类型拒收；仅返回内存预览，不写数据库。
- 以上都是离线合成数据测试，**不等于真实设备授权或图像脱敏能力已实现**。

## 仍需处理

现有 RawEvent/Provenance 缺少 consent_scope 与 dedupe_key 的完整持久化契约；安卓真机授权、图像级脱敏、撤权清理、独立 QA 尚未完成。Qwen launchd KeepAlive 为 true，DSH/WorkBuddy 异常退出可重启，模型预检仍有跨进程费用风险；本轮只读核查，未修改启动服务。Grok 继续禁用，未触碰 Docker、火山、手机或付费模型。

Radar Lite 的 R3 真实用户验收仍独立于数据源扩展，不得因本报告提高完成率。
