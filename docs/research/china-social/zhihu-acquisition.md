# 知乎数据采集路径验证

- 任务：`CN-DSH-001`（attempt 2）
- 目标：为 Content Opportunity Radar 验证知乎官方/公开采集路径
- 证据时间：2026-09-27 02:22 UTC
- 结论：**有条件 Go**。知乎开放平台提供文档化的搜索、热榜、问题回答和额度 HTTP API；生产接入应先取得知乎开发者 Access Secret，再用小限额请求验证账号权限、字段和配额。本次工作区没有知乎凭证，因此没有成功业务数据样本，也没有声称已验证账号额度。Cookie、浏览器自动化和未文档化页面接口不作为生产路径。

## 1. 证据边界

本报告把“实测请求”和“静态资料”分开：

| 标记 | 含义 |
| --- | --- |
| **实测** | 本工作区在上述时间向端点发出的无凭证 HTTPS 请求及其响应。没有使用 Cookie、浏览器自动化或个人账号。 |
| **静态资料** | 公开的知乎开发者文档入口，以及引用该入口的第三方 API 参考镜像。字段、参数上限、错误码和额度说明没有被本次有效凭证响应复核。 |
| **未知** | 当前证据不足，不能在实现或报告中当作已验证事实。 |

### 1.1 实测结果

请求只带 `Accept: application/json`，没有 `Authorization` 或 `X-Request-Timestamp`。四个端点都返回 HTTP 200，但 JSON 业务结果为 `Code: 20001`、`Message: Authorization failed`、`Data: null`。

| 能力 | 无凭证请求 | 实测结果 | 能推出什么 |
| --- | --- | --- | --- |
| 站内搜索 | `GET https://developer.zhihu.com/api/v1/content/zhihu_search?Query=AI&Count=1` | HTTP 200；`20001 Authorization failed` | DNS、HTTPS、路由和端点可达；匿名不能读取结果。 |
| 热榜 | `GET https://developer.zhihu.com/api/v1/content/hot_list?Limit=1` | HTTP 200；`20001 Authorization failed` | 端点可达；没有热榜条目样本。 |
| 问题回答 | `GET https://developer.zhihu.com/api/v1/content/question_answers?QuestionUrl=https%3A%2F%2Fwww.zhihu.com%2Fquestion%2F1&Limit=1` | HTTP 200；`20001 Authorization failed` | 端点可达；没有伪造问题或回答结果。 |
| 额度 | `GET https://developer.zhihu.com/api/v1/quota?APIIDs=zhihu_search%2Chot_list%2Cquestion_answers` | HTTP 200；`20001 Authorization failed` | 额度接口可达；本次不知道任何剩余额度或重置时间。 |

因此，本报告不包含“成功访问返回的知乎内容”或业务 fixture。下面的字段表、映射和配额说明均是静态资料/实现模板。

## 2. Radar 契约检查

本次实际检查了 [core.py](../../../content-opportunity-radar/core.py)、[pipeline.py](../../../content-opportunity-radar/pipeline.py) 和 [social.py](../../../content-opportunity-radar/social.py)：

- `core.py` 的采集边界是 `DataProvider.collect(CollectionRequest) -> CollectionResult`。标准事件 `RawEvent` 要求 `provider`、`source`、`acquisition_method`、`retrieved_at`，并支持 `external_id`、`url`、`title`、`text`、`author`、`published_at`、数值 `metrics` 和 `Provenance`。
- `CollectionResult` 可表达 `ProviderState.AUTH_REQUIRED`、`ProviderState.RATE_LIMITED`、warnings、分页 `cursor` 和 `RateLimit`。认证失败不应伪装成空的健康结果。
- `pipeline.py` 的真实 provider 使用 JSON HTTP 请求，将响应转换为 `RawEvent`；失败由 provider 隔离，成功事件才进入缓存和信号阶段。现有 `_remaining`/`_reset_at` 只解析标准 `X-RateLimit-*` 响应头，所以知乎没有这些头时应保留 `rate_limit: null`，不能估算。
- `pipeline.py` 当前默认 provider 列表没有知乎；本任务是采集路径验证，**没有修改 provider registry、Opportunity Score 或生产抓取代码**。
- `social.py` 用 `SocialObservation` 连接平台适配器和既有 `Signal`。可用指标包括 `QUESTION_COUNT`、`SEARCH_RANK`、`HOT_RANK`、`ENGAGEMENT`、`ENGAGEMENT_VELOCITY`、`CONTENT_SUPPLY` 等；`SEARCH_RANK`/`HOT_RANK` 是逆向指标。没有显式 `normalized_value` 且没有至少三条历史时，归一化结果是 unknown，不得补零或创建“Social Hot Score”。

## 3. 路径矩阵

分类描述采集路径的来源/授权属性，不代表内容天然可商用。

| 路径 | 必须标记 | 认证方法 | Radar 能力 | 限制与结论 |
| --- | --- | --- | --- | --- |
| `GET /api/v1/content/zhihu_search` | **official** | 静态资料称 `Authorization: Bearer <Access Secret>`，并带秒级 `X-Request-Timestamp`；本次无凭证实测为 `20001`。 | 站内搜索问题、回答、文章。 | 文档快照称 `Count` 默认 10、最大 10；需凭证验证。**首选**。 |
| `GET /api/v1/content/hot_list` | **official** | 同上；本次无凭证实测为 `20001`。 | 当前热榜问题/文章与榜位快照。 | 文档快照称 `Limit` 默认/最大 30；不等于历史趋势。**首选补充**。 |
| `GET /api/v1/content/question_answers` | **official** | 同上；本次无凭证实测为 `20001`。 | 指定问题的回答摘要和分页。 | 需要完整问题 URL；`Summary` 是摘要，不是全文；文档快照称 `Limit` 1..50。**首选补充**。 |
| `GET /api/v1/quota` | **official** | 同一 Access Secret；本次无凭证实测为 `20001`。 | 查询账号/API 的当前额度。 | 上线前必查；自然日时区、重置时刻和本账号数值未知。 |
| OAuth 授权后的用户数据 | **official**（authorized access） | OAuth 授权与获批 scope；本次未实测，未猜测 scope、PKCE 或 refresh 细节。 | 仅授权范围内的用户内容/收藏/关注等。 | 不能替代全站搜索或热榜；保留授权主体、scope 和审计记录。 |
| 直接读取 `www.zhihu.com` HTML、未文档化 `/api/*`、Cookie/浏览器自动化 | **public-web + experimental** | 没有稳定的官方开发者合同；可能依赖登录态、签名或风控处理。 | 只能作为一次性人工研究或页面核查。 | 不进入生产 provider registry，不称为 official，不与官方事件无标记混算。 |
| 第三方数据集/供应商 | **licensed**（只有合同成立后） | 需合同明确知乎来源、字段、用途、地域、缓存和再分发权。 | 可能补充历史热榜或搜索数据。 | 本次未核验任何供应商合同、覆盖或商用权；不能仅凭网页宣传标 licensed。 |
| GitHub CLI/镜像/Skill 包 | **experimental**（若实际调用官方 API，事件仍标 official） | 包装项目自身不是知乎授权；通常仍需 Access Secret。 | 可辅助开发和字段核对。 | 不提供 SLA 或授权证明；生产 provenance 必须记录真实知乎 endpoint。 |

## 4. 可用字段（静态资料，未由本次成功响应复核）

### 4.1 站内搜索

文档化端点：`GET /api/v1/content/zhihu_search`。请求字段：`Query`（必填）、`Count`（默认 10，最大 10；上限来自静态资料）。

响应 `Data` 字段：`HasMore`、`SearchHashId`、`Items`、`EmptyReason`。`Items[]` 字段：

- 内容：`Title`、`ContentType`、`ContentID`、`ContentText`、`Url`；
- 作者：`AuthorName`、`AuthorAvatar`、`AuthorBadge`、`AuthorBadgeText`；
- 互动/排序：`CommentCount`、`VoteUpCount`、`RankingScore`；
- 时间/其他：`EditTime`、`AuthorityLevel`、可选 `CommentInfoList`。

Radar 第一版建议只依赖 `ContentID`、`Url`、`Title`、`ContentText`、`ContentType`、`AuthorName`、`EditTime`、`VoteUpCount`、`CommentCount`。`RankingScore` 是平台排序字段，不直接当跨平台热度；`EditTime` 的单位和“发布时间/更新时间”语义必须用凭证响应确认。

### 4.2 热榜

文档化端点：`GET /api/v1/content/hot_list`。请求字段：`Limit`（默认/最大 30，静态资料）。响应 `Data`：`Total`、`Items`。`Items[]`：`Title`、`Url`、`ThumbnailUrl`、`Summary`；资料称当前包含问题和文章。

静态字段没有承诺数值热度、赞同数或评论数。数组位置可派生 `hot_rank = index + 1`，但只能表示抓取时刻的榜位；没有明确 engagement 字段时不要伪造 `ENGAGEMENT`。

### 4.3 问题回答

文档化端点：`GET /api/v1/content/question_answers`。请求字段：`QuestionUrl`（必填完整问题 URL）、`Offset`（默认 0）、`Limit`（静态资料称 1..50）。响应 `Data`：`Items`、`Paging`。`Items[]`：`ContentType`（`answer`）、`ContentToken`、`Url`、`Summary`。`Paging`：`IsEnd`、`NextOffset`，`Totals` 可选。

`Summary` 是服务返回摘要/截取文本，不是回答全文。必须按 `IsEnd` 和服务端 `NextOffset` 翻页；不能根据短页、空页或 `Totals` 自行推算结束。过滤掉无效或无摘要回答时，条目数可能少于 `Limit`。

## 5. 认证、配额和限流

### 已知（静态资料）

- 鉴权：Bearer Access Secret，加秒级 `X-Request-Timestamp`；`20001` 表示鉴权失败，`30001` 表示频率限制。
- `quota` 可按 API ID 查询 `TotalQuota`、`TotalUsed`、`RemainingQuota`；静态资料列出 `zhihu_search`、`hot_list`、`question_answers` 等。
- 请求参数上限是 `zhihu_search Count <= 10`、`hot_list Limit <= 30`、`question_answers Limit` 为 1..50；这不等于每日额度。

### 本次未知（必须用 Radar 凭证实测）

- 当前账号/租户的每日额度、自然日时区、重置时刻；
- 账号是否获批每项 API、scope/风控状态；
- 并发限制、重试安全性和内容新鲜度；
- `EditTime` 单位/语义、`SearchHashId` 生命周期；
- 是否稳定返回 `X-RateLimit-Remaining` / `X-RateLimit-Reset`。

实现时将 `20001` 映射为 `AUTH_REQUIRED`，明确的 `30001`/限流响应映射为 `RATE_LIMITED`；只有实际响应头可解析时才填写 `CollectionResult.rate_limit`，否则为 `null`。Access Secret 只能由运行时 secret 注入，不能进入 raw、缓存、日志或 provenance。

## 6. 标准化映射模板（非实测样本）

### 搜索结果 -> `RawEvent`

```text
provider: "zhihu"
source: "developer.zhihu.com"
acquisition_method: AcquisitionMethod.OFFICIAL_API
external_id: Items[].ContentID
url: Items[].Url
title: Items[].Title
text: Items[].ContentText
author: Items[].AuthorName
published_at: null  # 确认 EditTime 语义/单位后再填
retrieved_at: request_time
metrics:
  vote_up_count: Items[].VoteUpCount
  comment_count: Items[].CommentCount
raw: {content_type, search_hash_id, authority_level}
provenance:
  terms_class: "zhihu_official_api"
  api_version: "v1"
  endpoint: "GET /api/v1/content/zhihu_search"
```

### 热榜 -> `RawEvent`

```text
provider: "zhihu"
source: "developer.zhihu.com"
acquisition_method: AcquisitionMethod.OFFICIAL_API
external_id: stable_id("zhihu-hot", Items[].Url)
url: Items[].Url
title: Items[].Title
text: Items[].Summary
retrieved_at: request_time
metrics:
  hot_rank: array_index + 1
raw: {thumbnail_url: Items[].ThumbnailUrl, total: Data.Total}
provenance.endpoint: "GET /api/v1/content/hot_list"
```

### 问题回答 -> `RawEvent`

```text
provider: "zhihu"
source: "developer.zhihu.com"
acquisition_method: AcquisitionMethod.OFFICIAL_API
external_id: Items[].ContentToken
url: Items[].Url
text: Items[].Summary  # 摘要，不是全文
retrieved_at: request_time
raw: {content_type: "answer", question_url, paging}
provenance.endpoint: "GET /api/v1/content/question_answers"
```

随后再按窗口生成 `SocialObservation`：搜索/热榜数组位置对应 `SEARCH_RANK`/`HOT_RANK`；问题回答数量对应 `QUESTION_COUNT`（必须声明窗口）；同一接口明确返回的赞同数+评论数才可作为 `ENGAGEMENT` 组件；摘要缺失不能推断为零。`evidence_ids` 必须指向 `RawEvent.id`，`acquisition_method` 保持一致。

## 7. Provenance 推荐与决策

生产优先级：

1. **official**：取得并轮换 Access Secret；先调用 `quota`，再用最小 `Count`/`Limit` 验证搜索、热榜和问题回答；每个事件记录真实 endpoint、版本、请求时间、稳定外部 ID 和字段来源。
2. **official authorized**：只处理明确授权范围内的用户数据，审计记录保存授权主体和 scope，不能扩大为全站数据。
3. **licensed**：合同明确覆盖知乎来源、商业用途、字段、保存和再分发后才启用；`terms_class` 写供应商许可类别，不写 `official_api`。
4. **public-web/experimental**：只做人工或一次性研究，不加入生产 provider registry，不将页面字段当 API 合同。

最终建议：官方 API 作为可选、失败隔离的 provider，**先授权验证再编码**；将无凭证/额度不足映射为 provider 状态，不自动回退 Cookie/browser scraping。没有可核验合同的第三方数据不能标 licensed。任务不修改 Opportunity Score 架构。

## 8. 来源

官方入口（以当前页面为最终合同）：

- [知乎开发者文档目录](https://developer.zhihu.com/docs)
- [知乎额度文档](https://developer.zhihu.com/docs?key=quota)
- [知乎 OAuth 授权入口](https://openapi.zhihu.com/authorize)

静态交叉资料（不是官方授权证明，也不是本次业务响应）：

- [知乎开放平台 API 参考镜像](https://raw.githubusercontent.com/dawnswwwww/zhihu-cli/main/skills/zhihu-cli/references/api-reference.md)，标明其依据 `developer.zhihu.com/docs` 的抓取快照。
- [API 覆盖与边界](https://raw.githubusercontent.com/klarkxy/zhihu-search/main/docs/API_COVERAGE.md)，用于核对端点覆盖与未说明项。

本次实测的四个 endpoint URL 和完整业务错误结果已在第 1.1 节记录；没有凭证，因此没有成功数据样本可发布。
