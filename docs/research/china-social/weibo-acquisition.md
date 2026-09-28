# 微博数据采集路径验证 — Weibo Acquisition Validation

- 任务：`CN-DSH-002`（attempt 2，修复交付物注册：文件位于 workspace 根的 `docs/research/china-social/weibo-acquisition.md`）
- 目标：为 Content Opportunity Radar 验证微博官方/开放采集路径，覆盖 **search / trend / hot-list**。
- 证据时间：**2026-09-28 UTC**；attempt 2 于同日对第 1.1 节匿名探测做了独立复核，响应与首次记录一致。
- 结论：**对标准开放平台 No-Go，对商业数据 API 有条件 Go，对 public-web 仅 experimental。**
  - 现行标准开放接口索引（文档更新 2025-05-09）**已不包含任何 search / trends / hot 接口**；旧版 `2/search/topics` 属高级接口且官方明确“旧版接口不再接受申请”。
  - 唯一合规的微博搜索/热搜/话题路径是**微博商业数据 API**（`c.api.weibo.com`），需企业/商务接入、签约《商业数据服务合作协议》，多数接口收费。
  - 公开网页热搜端点（`weibo.com/ajax/...`、`s.weibo.com/top/summary`）在无 Cookie 匿名探测下返回 403/302，属 **public-web + experimental**，不得标为 official，也不进入生产 provider。
  - 本次工作区**没有任何微博凭证**，因此没有成功业务数据样本，也没有声称验证过任何账号配额；字段与配额说明均标注为“文档”而非“实测”。

---

## 1. 证据边界（documented vs observed）

| 标记 | 含义 |
| --- | --- |
| **实测** | 2026-09-28 UTC 由本任务运行环境发出的**匿名 HTTPS 请求**（无 Cookie、无 `access_token`、无登录态、无浏览器自动化）及其响应。 |
| **文档** | 微博开放平台官方 wiki / 商业数据 API 文档页的现行文本；记录 URL 与文档修订日期。 |
| **未知** | 证据不足，不得在实现或报告中当作已验证事实。 |

### 1.1 实测结果（匿名，无凭证）

请求只带常规浏览器 `User-Agent` 和 `Accept`，不携带任何 Cookie / OAuth token。

| 能力 | 端点 | 实测响应 | 能推出什么 |
| --- | --- | --- | --- |
| 标准-话题搜索 | `GET https://api.weibo.com/2/search/topics.json?q=%23AI%23&count=1` | HTTP **400**，`{"error_code":"10006","error":"source paramter(appkey) is missing"}` | 端点可达；匿名/缺 AppKey 不能读取结果。 |
| 标准-话题列表 | `GET https://api.weibo.com/2/trends.json?uid=1&count=1` | HTTP **400**，`10006 source paramter(appkey) is missing` | 端点可达；需 OAuth。 |
| 商业-热搜榜 | `GET https://c.api.weibo.com/2/search/hot_word/biz.json?count=1` | HTTP **403**，`{"error_code":21301,"error":"auth by Null spi!"}` | 需商业 OAuth；匿名无数据。 |
| 商业-话题检索 | `GET https://c.api.weibo.com/2/search/trends/name.json?q=AI&count=1` | HTTP **403**，`21301 auth by Null spi!` | 需商业 OAuth；匿名无数据。 |
| 商业-微博搜索 | `GET https://c.api.weibo.com/2/search/statuses/limited.json?q=AI&count=10` | HTTP **403**，`21301 auth by Null spi!` | 需商业 OAuth；匿名无数据。 |
| public-web 热搜 JSON | `GET https://weibo.com/ajax/side/hotSearch` | HTTP **403**，`{"error":"Forbidden"}` | 无 Cookie 匿名不可用。 |
| public-web 热搜 HTML | `GET https://s.weibo.com/top/summary?cate=realtimehot` | HTTP **302** 重定向，空 body | 匿名被重定向（登录/验证），不可作为稳定数据源。 |
| public-web 热搜 band | `GET https://weibo.com/ajax/statuses/hot_band` | HTTP **403**，`{"error":"Forbidden"}` | 无 Cookie 匿名不可用。 |

> 环境说明：以上为任务运行环境出口 IP 的匿名探测结果，可能受地域、风控和反爬策略影响；**只用于证明“匿名无凭证不可稳定读取”**，不代表持证/持 Cookie 的真实可用性。未做任何登录或 Cookie 注入。

### 1.2 已确认的官方文档事实

- 标准开放平台索引页（文档修订 **2025-05-09**，oldid=16672）列出的接口仅有：OAuth2、`account/*`、`users/*`、`statuses/*`、`comments/*`、`emotions`。**没有任何 `search/*`、`trends/*`、热搜接口。**
- 商业接口索引页（文档修订 **2026-09-18**，oldid=16860）列出 `search/statuses/limited`、`search/statuses/historical/*`、`search/hot_word/biz` 等商业数据接口（该索引页未单列 `search/trends/name`，但该页自身为正式文档页）。
- `2/trends` 页标题即“**获取某人的话题列表（即将废弃）**”，是**单个用户的话题列表**，不是全站热搜/趋势。
- `2/search/topics` 标注“**访问级别：高级接口（需要授权）**”；高级接口申请页（文档修订 2013-02-26）明确“**旧版接口不再接受申请**”。
- 商业数据 API 介绍页（文档修订 2025-05-08）说明：企业接入、**部分收费**、独立域名与服务资源，支持 REST 与订阅（推送）两种方式，联系 `businessapi@staff.sina.com.cn`。

---

## 2. Radar 契约检查

本次实际检查了 [`core.py`](../../../content-opportunity-radar/core.py)、[`pipeline.py`](../../../content-opportunity-radar/pipeline.py)、[`social.py`](../../../content-opportunity-radar/social.py)：

- `core.py` 的采集边界是 `DataProvider.collect(CollectionRequest) -> CollectionResult`。`RawEvent` 要求 `provider`、`source`、`acquisition_method`、`retrieved_at`，并支持 `external_id`、`url`、`title`、`text`、`author`、`community`、`published_at`、`language`、`country`、数值 `metrics` 与 `Provenance(terms_class, api_version, endpoint)`。
- `AcquisitionMethod` 已有 `OFFICIAL_API`、`OAUTH_API`、`PUBLIC_WEB_API`、`RSS`、`LICENSED` 等枚举，足以区分微博 official / public-web / licensed，**无需改架构**。
- `CollectionResult` 可表达 `ProviderState.AUTH_REQUIRED` / `RATE_LIMITED` / `DEGRADED` / `FAILED`、warnings、`cursor` 与 `RateLimit`。认证失败必须映射为 `AUTH_REQUIRED`，不能伪装成空的健康结果。
- `pipeline.py` 的 `_remaining`/`_reset_at` 只解析标准 `X-RateLimit-*` 响应头；微博未承诺这些响应头时应保持 `rate_limit: null`，不得估算。
- 现有 provider registry、Opportunity Score、归一化逻辑**未做任何修改**；本任务只产出研究文档。
- `social.py` 的 `SocialMetric` 已含 `SEARCH_RANK`、`HOT_RANK`（逆向指标）、`ENGAGEMENT`、`MENTION_COUNT`、`CONTENT_SUPPLY` 等，可将微博搜索结果与热搜榜接入既有 `Signal`，**不新增“Social Hot Score”**。

---

## 3. Search / Trend / Hot-list 能力矩阵

分类只描述采集路径的来源与授权属性，不代表内容天然可商用（见第 7 节）。

| 路径 | 分类 | 能力 | 认证 | 文档化限制 | 结论 |
| --- | --- | --- | --- | --- | --- |
| `GET https://c.api.weibo.com/2/search/statuses/limited.json` | **official**（商业/付费） | 按关键词搜索近期微博（时间/地域/类型/排序过滤） | OAuth `access_token`（商业应用）或 `source` AppKey | `count` 10–50，默认 10；单查询最多 **1000** 条、最多 20 页；`>1000` 结果为**估算**；正文 >140 字截断；下游 500ms 超时需重试 | **搜索首选**；需签约 |
| `GET https://c.api.weibo.com/2/search/statuses/historical/create.json`（另有 `check`/`download`） | **official**（商业/付费） | 创建历史全量检索任务并下载结果 | 同上（POST） | `q`≤1000 词；`starttime`/`endtime` 毫秒必填，单任务区间**≤1 个月**；可回溯至 2018；单任务下载**≤200 万**条；精确匹配；配额不足 1000 不能建任务 | **历史回溯唯一合规路径**；需签约 |
| `GET https://c.api.weibo.com/2/search/hot_word/biz.json` | **official**（商业/付费） | **官方热搜榜**榜单快照 | 同上 | `count` 最大 50，默认 50；`id=0` 为置顶；`num` 为热度 | **热榜首选**；需签约 |
| `GET https://c.api.weibo.com/2/search/trends/name.json` | **official**（商业/付费） | 按关键词检索**话题**（阅读数/讨论数） | 同上 | `q` 必填；`count` 最大 100，默认 5 | **话题/趋势首选**；需签约 |
| 商业订阅推送 `commercial/push` | **official**（商业/付费） | 按关键词/用户/域名实时推送新微博/评论 | 商业 OAuth + IP 白名单 | 关键词≤20,000（逻辑词≤1,000，单次≤50）；用户≤10,000；域名≤20；默认仅 **1%** 采样 | 适合持续采集；需签约 |
| `GET https://api.weibo.com/2/search/topics.json` | **official**（legacy/高级，事实停发） | 仅搜索 `#话题#` 内微博 | OAuth | `q` 必须在 `#...#` 之间；`count` 默认 10、最大 50；**仅返回最新 200 条** | **不推荐**；旧版接口不再接受申请 |
| `GET https://api.weibo.com/2/trends.json` | **official**（legacy，即将废弃） | 某个**用户**的话题列表 | OAuth | 需 `uid`；返回 `{num, hotword, trend_id}` | **不可用于全站趋势** |
| `GET https://api.weibo.com/2/statuses/public_timeline.json` | **official**（legacy） | 最新公共微博 | OAuth | 现行 API 索引已不列出该接口 | **视为已下线/不可用** |
| 标准 API 其余接口（`statuses/home_timeline`、`statuses/user_timeline`、`comments/*`、`users/*`） | **official** | 仅授权用户自身/关注数据 | OAuth | 无全站搜索/热榜能力 | 可做授权用户上下文，**不能替代全局搜索** |
| `weibo.com/ajax/side/hotSearch`、`weibo.com/ajax/statuses/hot_band`、`s.weibo.com/top/summary`、`m.weibo.cn/api/container/getIndex` | **public-web + experimental** | 页面内部 JSON/HTML 热榜 | 无官方合同；实际需 Cookie/登录/风控绕行 | 本次匿名实测 403/302 | **不进入生产 registry** |
| 第三方数据供应商 / 数据集 | **licensed**（仅合同成立后） | 可能补充历史/热搜 | 合同 | 本次**未核验任何合同、覆盖或再分发权** | 仅书面合同后启用 |
| MCP / SDK / CLI 包装 | **experimental**（若内部真调商业 API，事件仍标 official） | 辅助开发/字段核对 | 包装项目本身不构成授权 | 无 SLA/授权证明 | provenance 必须记真实微博 endpoint |

---

## 4. 认证方法

### 4.1 标准开放平台（`api.weibo.com/2`）

- OAuth 2.0：`oauth2/authorize` → `oauth2/access_token`；`access_token` 为必填参数。匿名缺 AppKey 实测返回 `10006`。
- 部分接口需 `scope`，用户单独授权后才能调用；应用无对应权限时返回 `10014 Insufficient app permissions`（需联系微博商务申请），用户拒绝返回 `10032`。
- 高级接口需在控制台“接口管理 > 申请权限”申请，审核约 3 个工作日；但官方明确**旧版接口不再接受申请**。

### 4.2 商业数据 API（`c.api.weibo.com` / `openapi.sc.weibo.com`）

- 同样走 OAuth `access_token`（商业应用）或 `source=AppKey`；匿名实测返回 `21301 auth by Null spi!`。
- 需企业/商务接入：商业数据 API 门户 <https://openapi.sc.weibo.com/>，联系 `businessapi@staff.sina.com.cn`。
- 商业接口页统一标注“请求所有商业接口：**50000 次/小时/IP**”。
- 开发者协议条款 **2.7.8**：业务范围为**数据采集、分析并服务于其他第三方**的开发者，**仅可通过微博商业数据 API**开展工作，并签订《商业数据服务合作协议》。Content Opportunity Radar 属此类，必须以商业 API 为合规基础。

### 4.3 配额 / 限流（标准平台，文档 2025-05-15）

- 单授权用户：**100 次/天**（累计调用开放接口）。
- 单 IP：**15,000 次/小时**。
- 写操作（发微博等）：单授权用户 **30 次/小时**。
- 未通过审核应用：仅允许授权 **15 个测试账号**。
- 商业平台的**账号级日配额、计费、并发**：**未知**，需签约后在控制台/合同确认。
- 商业 REST：文档只给 50,000 次/小时/IP；**按账号的真实配额与重置时刻未验证**。

### 4.4 明确标记为未知

- 本工作区无微博凭证：账号权限、scope 状态、真实配额、并发、计费均**未知**。
- `search/trends/name` 字段表中“话题阅读数”写作 `red`，而 JSON 示例用 `read`——**真实字段名以持证响应为准**。
- 微博是否稳定返回 `X-RateLimit-Remaining` / `X-RateLimit-Reset`：**未知**；无则 `rate_limit` 保持 `null`。
- 公开网页接口在持 Cookie/特定出口下的成功率：**未验证**，且不作为生产依据。

---

## 5. 可用字段（文档，未由成功响应复核）

### 5.1 搜索 `search/statuses/limited`

请求：`q`(必填, URLencode, 不能含 `{ } "`)、`ids`(≤50, `~`分隔)、`province`、`city`、`sort`(`time|hot|fwnum|cmtnum`)、`starttime`/`endtime`(秒)、`hasori/hasret/hastext/haspic/hasvideo/hasmusic/haslink/hasat/hasv`、`istag`、`dup`(默认 1)、`antispam`(默认 1)、`page`、`count`(10–50)、`base_app`。

响应 `{ "statuses": [...], "total_number": N }`。`statuses[]` 可用字段：

- 标识/时间：`id`、`mid`、`idstr`、`created_at`、`truncated`
- 内容：`text`(≤140字)、`source`、`geo`、`visible`、`pic_ids`、`ad`
- 作者：`user`(`id`、`screen_name`、`name`、`province`、`city`、`followers_count`、`verified`、`verified_reason` …)
- 互动：`reposts_count`、`comments_count`、`attitudes_count`
- 转发链：`retweeted_status`

### 5.2 热搜 `search/hot_word/biz`

响应：`cat`、`app_link`、`h5_link`、`data[]`：

- `id`：榜位（`0` = 置顶，置顶非固定位）
- `word`：搜索词
- `num`：搜索热度
- `flag`：`1` 新 / `2` 热 / `4` 爆 / `16` 沸 / `0` 无
- `h5_query_link`、`app_query_link`、`flag_link`

错误格式：`{request, error_code, error}`（如 `21400`）。

### 5.3 话题检索 `search/trends/name`

请求：`q`(必填)、`page`、`count`(最大 100，默认 5)。响应：

- 顶层：`time`、`query`、`num`、`bs_query`、`AbsStr`、`m`(结果数)
- `result[]`：`object_id`、`title`、`summary`、`image`、`weburl`、`scheme`、`read`(话题阅读数，见表/示例差异)、`mention`(话题讨论数)

### 5.4 历史检索任务 `search/statuses/historical/create`

请求：`q`/`ids`/`province` 至少一个；`starttime`/`endtime`(毫秒, 必填, 单段≤1个月, end ≤ 昨天最后一秒)、`type`、`hasv`、`onlynum`(1–100, 默认 100)。响应：`task_id`、`secret_key`（用于 `check` / `download`）。结果按时间倒序；返回字段与 `statuses` 类似。

---

## 6. 标准化 RawEvent / SocialObservation 映射（已用 Radar 契约验证）

### 6.1 `search/statuses/limited` 单条微博 → `RawEvent`

```text
provider: "weibo"
source: "c.api.weibo.com"
acquisition_method: AcquisitionMethod.OFFICIAL_API
external_id: statuses[].idstr
url: "https://weibo.com/{statuses[].user.id}/{statuses[].mid}"
title: statuses[].text[:40]
text: statuses[].text
author: statuses[].user.screen_name
community: null
published_at: parse(statuses[].created_at, "%a %b %d %H:%M:%S %z %Y")
language: "zh"
country: "CN"
metrics:
  reposts_count: statuses[].reposts_count
  comments_count: statuses[].comments_count
  attitudes_count: statuses[].attitudes_count
raw: {mid, user_id, is_retweet: bool("retweeted_status" in item), query}
provenance:
  terms_class: "weibo_commercial_api"
  api_version: "2"
  endpoint: "GET /2/search/statuses/limited"
```

序列化样例（`RawEvent.to_dict()`，已由本地校验脚本生成，值均为合成数据，不含真实用户信息）：

```json
{
  "id": "58aa6a23ed430deed04ea6de",
  "provider": "weibo",
  "source": "c.api.weibo.com",
  "acquisition_method": "official_api",
  "retrieved_at": "2026-09-28T00:00:00+00:00",
  "external_id": "EXAMPLE0001",
  "url": "https://weibo.com/1404376560/5000000000000001",
  "title": "示例微博正文 #AI#",
  "text": "示例微博正文 #AI#",
  "author": "example_user",
  "community": null,
  "published_at": "2011-05-31T09:46:55+00:00",
  "language": "zh",
  "country": "CN",
  "metrics": {"reposts_count": 8.0, "comments_count": 9.0, "attitudes_count": 3.0},
  "provenance": {
    "terms_class": "weibo_commercial_api",
    "api_version": "2",
    "endpoint": "GET /2/search/statuses/limited"
  }
}
```

> **尝试 2 复核（只读，未改评分架构）**：以上 JSON 经 `content-opportunity-radar/core.py` 的 `RawEvent.from_dict()` 反序列化并通过 `to_dict()` 回写，`acquisition_method` 解析为 `AcquisitionMethod.OFFICIAL_API`，必需字段齐全。另以 `social.py` 的 `SocialObservation(HOT_RANK, value=3)` 在提供 4 个历史点时经 `normalize_social_observations()` 产出 `momentum` Signal（逆向百分位 100）；不提供历史点时输出 `skipped=missing_normalization_or_history`，未被补零。该复核为一次性只读校验，不新增任何生产代码、provider 或评分逻辑。

### 6.2 其余两条路径的核心映射

```text
# 热搜 search/hot_word/biz -> RawEvent
provider: "weibo"; source: "c.api.weibo.com"
acquisition_method: AcquisitionMethod.OFFICIAL_API
external_id: stable_id("weibo-hot", data[].word)
url: data[].h5_query_link
title: data[].word
text: null
metrics: {hot_rank: data[].id, hot_heat: data[].num}   # hot_rank 为逆向指标
raw: {flag: data[].flag, cat: Data.cat}
provenance.endpoint: "GET /2/search/hot_word/biz"

# 话题检索 search/trends/name -> RawEvent
provider: "weibo"; source: "c.api.weibo.com"
acquisition_method: AcquisitionMethod.OFFICIAL_API
external_id: result[].object_id
url: result[].weburl
title: result[].title
text: result[].summary
metrics: {topic_read: result[].read, topic_mention: result[].mention}
raw: {scheme: result[].scheme, query: Data.query}
provenance.endpoint: "GET /2/search/trends/name"
```

### 6.3 到 `SocialMetric` 的映射（沿用 social.py，不改评分架构）

| 微博字段/派生量 | `SocialMetric` | 方向 |
| --- | --- | --- |
| `result[].read` / `result[].mention` | `MENTION_COUNT`（需声明窗口） | 正向 |
| 搜索命中的微博数（按窗口聚合） | `MENTION_COUNT` / `CONTENT_SUPPLY` | 正向 |
| `reposts_count + comments_count (+ attitudes_count)` | `ENGAGEMENT` 组件，或差分求 `ENGAGEMENT_VELOCITY` | 正向 |
| `data[].id`（热搜榜位） | `HOT_RANK` | **逆向** |
| 搜索/话题数组位置（若接口未给数值） | `SEARCH_RANK` | **逆向** |

**规则**：缺失字段不得补零；无显式 `normalized_value` 且历史点 < 3 时归一化结果为 unknown（`missing_normalization_or_history`）；`SEARCH_RANK`/`HOT_RANK` 仅表示抓取时刻的榜位，不等于历史趋势。

---

## 7. Fallback 建议（官方覆盖不足时）

1. **生产默认**：新增一个 `WeiboProvider` 边界，但**只有在取得商业数据 API 凭证后才启用**。无凭证时返回 `ProviderState.AUTH_REQUIRED`（对应 `10006`/`21301`/`10014`），配额/限流返回 `RATE_LIMITED`，失败隔离、不影响其它 provider。
2. **搜索**：唯一合规生产路径是商业 `search/statuses/limited`；若暂无合同，用非微博跨平台证据（现有 `google_news`、`gdelt`，以及知乎官方 API）补足 demand/momentum，不要用 Cookie 抓取微博。
3. **热搜榜**：唯一合规路径是商业 `search/hot_word/biz`；合同未就绪时把 `HOT_RANK` 显式标为 **unknown**，不要用 public-web 端点冒充。
4. **趋势/话题**：用商业 `search/trends/name`（阅读/讨论）替代已废弃的 `2/trends`；`2/trends` 仅是个别用户话题列表，不接入。
5. **历史回溯**：如确需历史，用商业 `search/statuses/historical/*`（≤1 个月/任务、≤200 万/任务），比逐日实时抓取更合规、更稳定。
6. **降级链**：商业 API → 已授权的第三方数据供应商（licensed，需合同）→ 非微博公开源；**public-web/浏览器自动化永不进入生产 registry**。
7. **时长/成本**：签约前不要以商业 API 承诺 SLA；持续采集优先用订阅推送（默认 1% 采样，需要更高比例需单独申请）。

---

## 8. Provenance / 稳定性 / 商用风险

### 8.1 Provenance 与商用（开发者协议，文档修订 2022-06-09）

- **1.6 / 1.7**：用户数据与平台运营数据是微博（微梦公司）的**商业秘密**。
- **2.5.6 / 2.5.9**：不得将用户数据或平台数据以任何形式提供给第三方；仅限本应用/本服务内使用。
- **2.5.13 / 2.5.14**：运营数据权利归微梦公司；对外提供给合作伙伴须**书面同意**。
- **2.5.15**：停止使用开放平台或服务终止时，必须**立即删除全部**从开放平台获得的数据。
- **2.7.7**：通过开放 API 获得的数据**仅可服务于接入应用/网站，不得用于其它用途**。
- **2.7.8**：**数据采集、分析并服务于第三方的业务，仅可通过商业数据 API 并签订《商业数据服务合作协议》**——这是 Radar 方案定性的核心条款。
- **5.2**：禁止未经用户同意收集、编辑、出售或传播用户隐私信息；禁止避开内容保护机制（2.8 系列）。
- 频次页（2025-05-15）明确：**禁止第三方服务器端存储用户数据**；过度/机器人调用会导致 **appkey、IP 被封禁**。

**含义**：Radar 若要缓存/再分发微博事件，必须走商业数据 API + 合同，并把 `provenance.terms_class` 写成合同许可类别（如 `weibo_commercial_api`），**不得写成 `official_api` 免费接口**。

### 8.2 稳定性风险

- 商业搜索接口官方 FAQ 自述：下游 500ms 超时需重试；相同参数结果会变（约 5% 波动属正常）；>1000 结果为估算；分页受动态过滤影响会少于 `count`；`403` = 未登录/超频/超发布上限。
- 热搜榜是**快照**，非历史趋势；置顶位不固定。
- 免费/legacy 路径（`search/topics`、`trends`、`public_timeline`）文档极其陈旧（2012–2015）且不再接受申请，随时可能彻底下线。
- public-web 端点无合同、无 SLA、易被风控（本次匿名 403/302），字段与结构随时变化。

### 8.3 Radar 侧建议

- 每个事件记录真实 `endpoint`、`api_version`、`terms_class`、`retrieved_at`、稳定外部 ID 与字段来源。
- 无凭证/无权限时写入 `AUTH_REQUIRED`，无 `X-RateLimit-*` 时 `rate_limit=null`；不估算额度。
- 凭证只经运行时 secret 注入，**不进入 `raw`、缓存、日志或 provenance**。
- 不修改 Opportunity Score 架构；微博只作为新增、可失败隔离的 provider。

---

## 9. 文档 vs 实测 分离汇总

| 项目 | 文档 | 实测（匿名） |
| --- | --- | --- |
| 标准搜索 `search/topics` 存在且为高级接口 | ✅ 文档页 | ✅ 端点可达，`10006`（缺 AppKey），**无数据** |
| 标准 `trends` 是个别用户话题列表、即将废弃 | ✅ 文档页 | ✅ 端点可达，`10006`，**无数据** |
| 商业搜索/热搜/话题需商业 OAuth | ✅ 商业接口页 | ✅ `403 / 21301`，**无数据** |
| 商业接口 50,000 次/小时/IP | ✅ 各商业接口页 | ❌ 未验证（无凭证） |
| 账号级日配额、计费、并发、重置时刻 | ⚠️ 需合同 | ❌ 未知 |
| public-web 热搜可匿名读取 | ❌ 无官方承诺 | ✅ **不可**：403/302 |
| `read` vs `red` 字段名、`X-RateLimit-*` 头 | ⚠️ 文档不一致/未承诺 | ❌ 未知 |
| 成功业务数据样本 | — | ❌ 无凭证，未产出（不伪造） |

---

## 10. 来源（访问日期 2026-09-28 UTC）

官方文档（以当前页面为最终合同）：

- 标准 API 索引（文档 2025-05-09）：<https://open.weibo.com/wiki/API>
- 接口访问频次权限 / 存储与封禁（文档 2025-05-15）：<https://open.weibo.com/wiki/接口访问频次权限>
- 高级接口申请（文档 2013-02-26）：<https://open.weibo.com/wiki/高级接口申请>
- 旧版话题搜索 `2/search/topics`（文档 2012-11-16）：<https://open.weibo.com/wiki/2/search/topics>
- 旧版 `2/trends`（文档 2013-06-26）：<https://open.weibo.com/wiki/2/trends>
- 旧版 `2/statuses/public_timeline`（文档 2015-03-30）：<https://open.weibo.com/wiki/2/statuses/public_timeline>
- 商业数据 API 介绍（文档 2025-05-08）：<https://open.weibo.com/wiki/商业数据API>
- 商业接口 REST 索引（文档 2026-09-18）：<https://open.weibo.com/wiki/Business_API文档>
- 商业搜索 `C/2/search/statuses/limited`（文档 2018-01-31）：<https://open.weibo.com/wiki/C/2/search/statuses/limited>
- 商业历史检索 `C/2/search/statuses/historical/create`（文档 2024-04-29）：<https://open.weibo.com/wiki/C/2/search/statuses/historical/create>
- 商业热搜 `C/2/search/hot_word/biz`（文档 2021-09-10）：<https://open.weibo.com/wiki/C/2/search/hot_word/biz>
- 商业话题检索 `C/2/search/trends/name`（文档 2021-09-10）：<https://open.weibo.com/wiki/C/2/search/trends/name>
- 订阅服务手册（文档 2025-05-08）：<https://open.weibo.com/wiki/订阅服务手册>
- 商业数据常见问题（文档 2025-05-08）：<https://open.weibo.com/wiki/微服务常见问题>
- 开发者协议（文档 2022-06-09）：<https://open.weibo.com/wiki/开发者协议>
- Scope 授权（文档 2025-12-05）：<https://open.weibo.com/wiki/Scope>
- 商业数据 API 接入门户：<https://openapi.sc.weibo.com/>

本次匿名实测的 8 个 endpoint URL 与响应摘要已记录在第 1.1 节；无凭证，故无成功业务样本可发布。
