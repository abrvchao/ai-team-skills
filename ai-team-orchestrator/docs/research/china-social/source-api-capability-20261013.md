# SOC-API-001｜五平台官方数据接口可用性与成本核查（2026-10-13）

> 状态：官方文档与平台开放权限路径已验证；**实际凭据、调用权限、商业价格和线上响应均未验证**。本报告只查公共开发者文档，不曾调用任何平台 API，也未充值或申请凭据。此前 MiMo 任务 task_9aa6dd936922c6b8a33e 由于 model_empty_response 失败；本报告由独立只读证据调研补齐，不冒称该 Worker 完成。

## 一、验证层级

- **Documented（有官方文档）**：确认官方接口文档、Scope 和用途。
- **Authorized（已授权）**：须提供真实应用审批及访问权限证据；当前五平台均 **NOT VERIFIED**。
- **Live（已调通）**：须取得真实获准的接口响应、返回字段及用量证据；本轮均 **NOT VERIFIED**。
- **Cost gate（费用门禁）**：真实平台 API 默认禁用；即使标记免费试用，也要核实结算帐号、单次费率和自动充值设置，不允许无预算上线。

## 二、平台矩阵

| 平台 | 官方证据 | 可提供的 Radar 信号 | 权限 / 费用边界 | 判断 |
| --- | --- | --- | --- | --- |
| 知乎（P0） | [开发者开放平台](https://developer.zhihu.com/)：搜索、热榜 API；Bearer Access Secret 和 X-Request-Timestamp | 合法授权下的公开问题、热榜、话题与链接 | 支持个人中心新建 Access Secret，但实际调用额度、计费价格与商业许可尚未核实；**不得直接请求线上数据** | **CONDITIONAL GO：待核权限、费用、协议** |
| 抖音（P0） | [视频搜索文档](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/douyin-search-capability/aweme-dy-video-search)：GET /dy_open_api/v1/search/video/，Scope aweme.dy.video_search | 授权返回的关键词视频与允许的公共指标 | 需视频垂搜应用权限、client_token，接口要求 device_id。未确认开发者资格、合法设备标识来源及价格 | **CONDITIONAL GO：先确认获批权限** |
| B站（P1） | [官方开放平台](https://openhome.bilibili.com/doc)、[授权稿件查询](https://openhome.bilibili.com/doc/4/d9554788-dcef-f139-6217-b487d41c3826)：Scope ARC_BASE | 授权 UP 主稿件和部分表现 | 需要平台身份与创作者授权；授权稿件查询**不等于全站关键词搜索**，费率与批量限额未验证 | **CONDITIONAL GO：仅授权创作者数据** |
| 小红书（P1） | [开放授权范围](https://openaccount.xiaohongshu.com/docs/scope)、[API 目录](https://openaccount.xiaohongshu.com/docs/api-reference) | 获许可、用户同意范围内可见的公开选题信号 | 官方文档 2026-03-29 明确**仅 basic_info 已开放**，read_notes 仍规划中；OAuth 登录不等于笔记搜索 | **NO GO：官方 OAuth 不满足笔记搜索需求** |
| 闲鱼（P1） | [淘宝开放平台闲鱼商品查询](https://developer.alibaba.com/docs/api.htm?apiId=49488)：alibaba.idle.isv.item.query | 获授权服务商的自有商品标题、价格信号 | 文档标注“开放平台免费 API”，但**需要授权且聚石塔内调用**；不是全站二手商品关键词搜索 | **CONDITIONAL GO：仅获授权商家数据** |

## 三、与 Radar 产品现状的边界

1. 当前 source_intake.py 的 SourceGrant 与 accept_candidate 能完成**离线合成数据**的来源、费用、授权、域名白名单、指标、去重验证。必须输出 staged_only 和 is_production_raw_event=False，不宣称已经采集任何平台数据。
2. 最先可考虑知乎搜索或热榜，但须先获得应用授权、明确费用及预算、关闭自动充值并限制请求；随后核实抖音视频垂搜申请资格。未确认费用之前不调用真实接口。
3. B站仅优先考虑有明确创作者授权的数据；小红书、闲鱼不做未经许可的全站搜索抓取，更不绕过反爬虫或读取 App 私有数据库。
4. 安卓 P2 只有用户本人设备授权后开展；读取用户主动展示的可见页面时应最小化截图、脱敏、保留来源、允许撤权，仍先进入 staging，不自动跳转为生产 RawEvent。
5. 留存 platform、source_id、captured_at、provenance、grant_ref、dedupe_key；不写 Cookie、API Key、私信或完整私有 HTML。撤权后停止采集并按留存政策清理。

## 四、待外部授权事项（不影响离线研发）

- 知乎：Access Secret 所属账户、服务报价、配额、数据存储与商业使用许可。
- 抖音：开放平台应用与 aweme.dy.video_search 资格、device_id、费用及调用限额。
- B站：UP 主授权、ARC_BASE 申请和可保留字段。
- 小红书与闲鱼：搜索能力无通用官方接口授权证据；优先许可数据商，安卓仅在用户亲自许可后验证。

五个平台目前都不是 Authorized+Live；不得因此增加 Radar R3 真实用户验证完成数。

## 五、查证与成本确认

查证时间：2026-10-13 北京时间。资料均为公开官方文档，未发送任何平台业务 API 请求或模型推理请求。各平台实际成本与使用条款应在正式接入时重新确认；价格不清楚的字段统一为 **UNKNOWN**，不得默认免费。
