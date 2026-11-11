AI_TEAM_ARTIFACT_STATUS: ready_for_supervisor

# SOC-ANDROID-001 安卓授权只读采集可行性核验

- Task（任务）: `task_1a100a09ec8c8221204f` / 源任务 `SOC-ANDROID-001`
- Program（项目）: `china-social-source-expansion-v1`（`tasks/china-social-source-expansion-20261012.json`）
- Date（日期）: 2026-10-13
- Owner（执行）: dsh
- Scope（范围）: **只读代码审查 + 本地工具查询 + 公开官方文档核验 + 离线架构评审**；零业务平台请求、零设备写入
- 结论摘要: **Conditional Go（有条件可行）** —— 官方授权接口为唯一首选；安卓屏幕证据在当前代码下**只能进暂存区，不能进 RawEvent**；P2 真机采集**尚无用户本人授权，待验证（blocked_by_device）**。

---

## 0. 一句话结论

**当前 AndroidDeviceWorker 只能采集「设备元信息 + 一张全屏 PNG」，没有任何 UI 节点提取或 OCR 能力，也没有 consent（授权）字段和暂存区落库通道。** 因此五个平台中**没有任何一个**可以在今天用安卓屏幕路径产出可入库事件。官方 API 才是首选路径；屏幕证据在 P2 只可用于**人工核对/研究**，且必须先补齐 consent 记录与 staging 落库管道。

**官方 API 侧**：本次通过 `curl` 直取官方文档做了真实核验 —— **知乎是五平台中唯一"绿灯"**（文档完整、价格公开、有额度自查接口）；**小红书与闲鱼的官方 API 路径已被证据否决**；抖音有接口但需能力审批；B站文档不可达、无法断言。

> **重要声明：尚无安卓真机授权，P2 待验证。**
> 本报告未连接、未截屏、未点击任何设备。第 2 节的设备存在性结论来自**主机侧只读查询**（`adb devices -l`），**不构成**用户对本任务的采集授权。

---

## 1. 证据基线（Evidence Baseline）

### 1.1 已读取的真实文件

| 类型 | 路径 | 用途 |
| --- | --- | --- |
| 任务定义 | `tasks/china-social-source-expansion-20261012.json` | 五平台范围、阶段 P0–P3、`implementation_rules` |
| 历史任务 | `tasks/china-social-v1.json` | CN-DSH-001/002/003/004、CN-GEMINI-001 状态与结论 |
| Worker 文档 | `android-worker/README.md` | 官方声明的只读能力边界 |
| Worker 实现 | `android-worker/android_runtime.py` | 唯一的安卓执行逻辑（129 行） |
| Worker 测试 | `test_android_worker.py` | `parse_devices` / registry 断言 |
| E2E 监视器 | `android_e2e_watcher.py` | 设备等待、提交、artifact 校验 |
| 注册表 | `registry.py` | `android` capability 列表（第 88–89 行） |
| 桥接 | `bridge.py` | artifact 引用规范化（第 304–319 行）、agent 白名单（第 1129 行） |
| 事件契约 | `../content-opportunity-radar/core.py` | `RawEvent` / `AcquisitionMethod` / `Provenance` |
| 暂存闸门 | `../content-opportunity-radar/source_intake.py` | **既有** staging 实现（150 行） |
| 暂存测试 | `../content-opportunity-radar/test_source_intake.py` | 含 `android_screen` 用例（第 73–81 行） |
| 持久化 | `../content-opportunity-radar/collector.py` | `_upsert_event`、脱敏、schema |
| 社会信号 | `../content-opportunity-radar/social.py` | `SocialObservation` / 指标白名单 |
| CN-DSH 证据 | `../docs/research/china-social/zhihu-acquisition.md` | 知乎官方路径实测（无凭证 `20001`） |
| CN-DSH 证据 | `../docs/research/china-social/m2-controlled-experiment.md` | 微博/抖音/小红书匿名边界 |
| CN-DSH 证据 | `../docs/research/china-social/weibo-acquisition.md` | 微博商用接口结论 |
| 阶段记录 | `../docs/AI_TEAM_M2_REAL_WORLD_001.md` | 安卓执行器历史状态 |
| 运行状态 | `.orchestrator/android-e2e/state.json` | 唯一一次通过的 E2E 记录 |
| 真机产物 | `.android-e2e-smoke/.../task_c45eaaf30064672952e2/` | `android_report.json` + `android_screen.png` |

### 1.2 既有真机 E2E 实测产物（历史证据，非本次采集）

`.orchestrator/android-e2e/state.json` 记录了一次**已通过**的验收（2026-10-06）：

```json
{
  "status": "passed",
  "task_id": "task_c45eaaf30064672952e2",
  "devices": ["e2dec9dd"],
  "validation": { "ok": true, "failures": [] }
}
```

对应 `android_report.json` 内容：

```json
{
  "action": "inspect_device", "mode": "read_only", "runtime": "adb",
  "serial": "e2dec9dd", "model": "PJX110", "manufacturer": "OnePlus",
  "android_version": "16", "sdk": "36",
  "screenshot_available": true, "screenshot_bytes": 1430509
}
```

截图 `.android-e2e-smoke/.ai-team-worker/task_c45eaaf30064672952e2/android_screen.png` 经核验为 **PNG 1080×2376，1,430,509 bytes**。

**关键观察（对边界设计极其重要）**：该截图内容是一个应用商店的「小红书」搜索结果页，画面上包含：
- 状态栏的**通知图标**（多个 App 通知）与个人网络状态；
- 真实的**下载量/评分**等第三方展示数据；
- 底部的**广告位**内容。

这证明两件事：
1. **全屏截图必然携带与采集目标无关的个人上下文**（通知、账号相关界面元素），因此"截图即证据"必须配合脱敏与最小化，不能整屏直接入库；
2. **该 PNG 里的文字对系统完全不可读** —— 当前 worker 没有 OCR，也没有 UI 节点 dump，所以"屏幕上的小红书搜索结果"目前**无法转化为任何结构化字段**。这是本次核验最重要的能力缺口。

---

## 2. 真实能力核验：AndroidDeviceWorker 到底能做什么

### 2.1 唯一的执行入口

`android-worker/android_runtime.py::execute()`（第 69–117 行）是全部逻辑：

```python
action = str(metadata.get("action") or "inspect_device")
if action != "inspect_device":
    raise ValueError(f"Unsupported read-only Android action: {action}")
```

**只支持一个 action：`inspect_device`。** 传入任何其他 action 直接抛错。

### 2.2 穷举的 ADB 调用面

对 `android_runtime.py` 做全量检索，worker 触达 ADB 的命令**只有 4 处**：

| 行号 | 命令 | 读/写 | 说明 |
| --- | --- | --- | --- |
| 48 | `adb devices -l` | 只读 | 枚举设备，仅接受 `state == device`（第 31–32 行） |
| 65 | `adb -s <serial> shell getprop <name>` | 只读 | 读取 4 个属性 |
| 88–91 | `getprop ro.product.model` / `ro.product.manufacturer` / `ro.build.version.release` / `ro.build.version.sdk` | 只读 | 构成 report 的 4 个字段 |
| 97 | `adb -s <serial> exec-out screencap -p` | 只读 | 取当前屏幕一张 PNG |

对 `android-worker/` 与 `android_e2e_watcher.py` 检索 `uiautomator` / `dumpsys` / `input tap` / `input text` / `am start` / `monkey` / `install` / `uninstall`：**零命中**。

> 这从代码层面证实 README 的声明：**不点击、不输入、不安装、不卸载、不授权、不发布**。

### 2.3 能力矩阵（实测 = 读代码所得，非推测）

| 能力 | 现状 | 代码依据 | 缺口 |
| --- | --- | --- | --- |
| 设备发现与授权态判定 | ✅ 有 | `parse_devices()` L13–33；只收 `state=device`，`unauthorized`/`offline` 被丢弃 | 无 |
| 设备元信息 | ✅ 有（4 字段） | L88–91 | 缺 `ro.build.version.security_patch`、屏幕分辨率/密度等 |
| 全屏截图 | ✅ 有 | L96–107；`exec-out screencap -p` | 无区域裁剪、无多屏、无前后台区分 |
| **UI 节点/可访问性文本提取** | ❌ **无** | 全仓无 `uiautomator dump` | **致命缺口**：屏幕文字拿不出来 |
| **OCR / 文字识别** | ❌ **无** | 无任何 OCR 依赖或调用 | **致命缺口** |
| **页面来源识别（当前前台 App / 包名）** | ❌ **无** | 无 `dumpsys window`、无 `getprop` 前台包 | 无法证明截图属于哪个平台 |
| **采集时间戳（设备侧）** | ❌ **无** | report 无 `captured_at` | 只能靠宿主文件 mtime |
| **consent / 授权依据字段** | ❌ **无** | report 仅 `mode: read_only` | **合规缺口** |
| **原始证据哈希** | ❌ **无** | 未对 PNG 求 sha256 | 与任务 rule 4 冲突 |
| 截图与事件的对应关系 | ❌ **无** | 无 event 概念 | 无法审计 |
| 多设备 | ✅ 有（安全） | L59–60：多设备必须显式指定 `serial`，否则报错 | 设计正确 |
| 设备状态变更 | ❌ 无（设计如此） | 无写命令 | 正确边界 |

### 2.4 关键结论

> **当前 worker 的"只读证据"= 设备身份证 + 一张不可解析的全屏照片。**
> 它足以证明"设备在、屏幕能读"，**远不足以**支撑任何平台的业务字段采集。

---

## 3. 本地工具与设备授权状态核验（仅主机侧只读查询）

### 3.1 ADB 工具链

| 项 | 值 |
| --- | --- |
| 二进制 | `/opt/homebrew/bin/adb` → `Caskroom/android-platform-tools/37.0.1` |
| 版本 | Android Debug Bridge 1.0.41 / 37.0.1-15733141 |
| 宿主 | Darwin 27.0.0 (arm64) |
| 当前 shell PATH | **不含 adb**（`adb: command not found`） |
| worker 的解析方式 | `_adb_bin()` L36–40：`$ADB_BIN` 优先，否则 `shutil.which("adb")` |

**缺口 A（环境）**：worker 依赖的 `ADB_BIN` 在普通 shell 中未设置。LaunchAgent 脚本 `~/.config/ai-team/run-android-e2e-watcher.sh` 内部显式设置了 `export ADB_BIN=/opt/homebrew/bin/adb`，所以**后台服务可用**，但**任何手工/临时调用都会因找不到 adb 而失败**。这是运维脆弱点。

### 3.2 设备与授权状态

```
$ /opt/homebrew/bin/adb devices -l
List of devices attached
e2dec9dd   device usb:37748736X product:PJX110 model:PJX110 device:OP5D06L1 transport_id:1
```

- 存在 **1 台**设备：`e2dec9dd`（OnePlus `PJX110`）。
- 状态是 `device`（**已授权**），不是 `unauthorized`。
- 该序列号与 2026-10-06 通过 E2E 的设备**一致**。

**这条记录的真实含义（必须严格区分）**：
- ✅ 说明主机侧 adb 可用、设备当前物理连接、且该设备**曾在操作系统层面接受过 ADB 调试授权**；
- ❌ **不等于**用户授权本任务对该设备进行小红书/闲鱼等平台的采集；
- ❌ **不等于** P2 已启动。任务规则明确：*"Android 仅在用户本人授权设备/账户后，读取用户主动展示的页面"*（`implementation_rules` 第 2 条）。

> **本报告未执行**：`getprop`、`screencap`、任何 shell 交互，也未触碰设备。`adb devices -l` 是**纯主机侧**枚举，不改变设备状态。这符合"用户未明确授权时不得碰设备"。

**注意**：上面 `adb devices -l` 触发了 `adb` 守护进程自动启动（`* daemon not running; starting now at tcp:5037`）。这只是主机上的调试桥服务进程，不是对手机的操作。此处如实记录。

### 3.3 后台服务与注册状态

| 项 | 状态 | 依据 |
| --- | --- | --- |
| `ai.team.android-e2e` LaunchAgent | 已安装（`~/Library/LaunchAgents/ai.team.android-e2e.plist`） | plist 存在 |
| watcher 日志 | `/tmp/ai-team-android-e2e.{out,err}.log` 均为 **0 字节** | 无输出 |
| E2E 状态文件 | `status: passed`，**更新于 2026-10-06 11:31** | `.orchestrator/android-e2e/state.json` |
| Bridge 注册表中 android | `configured: true` / `available: false` / `queueable: true` | `.orchestrator/team-runs/_worker-roster.json` |

**缺口 B（真相安全）**：`state.json` 是**终态 `passed`**，而 `android_e2e_watcher.py::step()` 第 130–131 行对 `passed`/`failed` **直接提前返回**：

```python
if state.get("status") in {"passed", "failed"}:
    return state
```

也就是说，**watcher 已经永久停止推进**。它不会因为现在设备重新出现而再次采集。要重跑必须 `--reset`。这不是 bug（避免重复提交），但意味着**"设备已连接"与"E2E 会再次执行"是两件事**，任何报告都不得把当前设备在线误读为 E2E 正在运行。

### 3.4 真实失败样本（诚实记录）

`.android-e2e-smoke/.ai-team-worker/task_e132e2d7b28593e14e0e/stderr.log` 保留了一次**真实失败**：

```
RuntimeError: no authorized Android device connected
```

这是"无设备时必须失败、不得伪造成功"规则的**正面证据**，应作为回归基线保留。

---

## 4. 五平台采集方法矩阵

**判定原则**（来自 `implementation_rules`）：
1. 官方开放平台/被授权数据服务**优先**；
2. "文档存在"≠"我们已获批"，两者必须分列；
3. 安卓屏幕路径**仅备选**，且必须用户本人授权；
4. 默认**关闭**任何付费调用。

### 4.1 平台 × 路径总表

| 平台 | 首选路径 | 官方入口 | 文档化能力（核验状态） | 我们是否已获批 | 安卓屏幕备选 | 本阶段判定 |
| --- | --- | --- | --- | --- | --- | --- |
| **知乎** | `official_api` | developer.zhihu.com | 搜索 / 全网搜索 / 热榜 / 问答 / 直答 / 知识库 / 用户数据 ✅**已核验** | **UNKNOWN**（无 Access Secret） | 仅人工核对 | **Go（有凭证后）** |
| **抖音** | `official_api_with_approval` | developer.open-douyin.com | `aweme.dy.video_search` 关键词视频搜索 ✅**已核验** | **UNKNOWN**（能力需控制台申请） | 仅人工核对 | **Conditional Go** |
| **B站** | `official_api_authorized_creator` | openhome.bilibili.com | ⚠️ **未核验**（SPA 空壳）；旁证示企业实名+审核+**邀请授权** | **UNKNOWN** | 仅人工核对 | **Conditional Go**（范围受限） |
| **小红书** | `licensed` 或 opt-in 设备 | openaccount.xiaohongshu.com | ✅**已核验**：仅 `basic_info`；`read_notes` **规划中**；**无笔记搜索** | **UNKNOWN**（且内容权限对所有人未开放） | **唯一现实路径**，需 consent | **No-Go（官方 API）** / Conditional（设备） |
| **闲鱼** | 授权服务商 API 或 opt-in 设备 | developer.alibaba.com | ✅**已核验**：`item.query`=按ID查询；`spu.search`=品牌型号字典 | **UNKNOWN**（ISV/已验货计划） | 仅人工核对 | **No-Go（官方API）** → Conditional |

### 4.2 逐平台说明

#### 知乎（zhihu）— Conditional Go（五平台中最优）
- **官方能力**：`zhihu_search`、`global_search`、`hot_list`、`question_answers`、`zhida`、知识库、创作能力、用户数据。
- **实测证据（CN-DSH-001，`../docs/research/china-social/zhihu-acquisition.md`）**：无凭证请求 4 个端点均返回 HTTP 200，但业务体为 `Code: 20001 / Authorization failed`。**端点可达 ≠ 数据可读。**
- **2026-10-13 追加核验**：文档 JSON 全文已取到（135 KB），确认 Bearer + `X-Request-Timestamp` 鉴权、`/api/v1/quota` 额度接口、以及**公开价格**（搜索 20 元/千次、热榜 10 元/千次）。
- **为什么是 Conditional Go**：**五平台中唯一**同时具备"文档完整 + 价格公开 + 端点实测可达 + 有额度自查接口"的路径。
- **重要限制**：`zhihu_search` **单次最多 10 条**且 `HasMore` **固定 `false`** —— **无法深度翻页**。这会影响 Radar 的批量枚举设计，需在 P1 重新评估"关键词矩阵 + 高频轮询"替代"深翻页"。
- **不能做的**：不得把无凭证 `20001` 包装成"采集成功"；不得用 Cookie/浏览器自动化替代。
- **下一步**：申请 Access Secret → 用 `/api/v1/quota` 确认免费额度 → 小 `Count` 试调 → 核对价格是否接受。

#### 抖音（douyin）— Conditional Go（强依赖能力审批）
- **官方能力（已核验）**：`GET https://open.douyin.com/dy_open_api/v1/search/video/`，**Scope `aweme.dy.video_search`**，**权限要求"抖音视频垂搜（对外）"**。
- **关键风险**：**公开文档 ≠ 应用已获该能力**。能力需在控制台**单独申请开通**，未获批会返回能力类错误码。
- **定价**：**未公开价格表**，采用"审批给默认额度 + 另行提额"模式。
- **⚠️ 未证实**：外部资料流传的"审核 5–7 个工作日"**在本次抓取的页面中未找到**，不作为结论。
- **历史证据**：`CN-DSH-003` 状态仍为 `queued_not_started`；`m2-controlled-experiment.md` 记录匿名 HTTP 仅拿到 ~72KB 页面壳，**明确不构成数据合同**。
- **下一步**：先在控制台确认主体资质能否申请"抖音视频垂搜（对外）"。若资质不符 → **No-Go**。

#### B站（bilibili）— Conditional Go（范围严重受限，且证据最弱）
- **⚠️ 文档正文未能取到**（SPA 空壳 + scope 接口 404）。本报告**不把推断当事实**。
- **可确认的旁证**（来自 JS bundle）：
  - 平台流程为 **"身份信息填写 → 审核通过 → 创建应用"**；
  - 获授能力举例是 **"稿件分发等开发者能力"**；
  - 存在 **"如果您是被授权用户，请前往【管理中心】-【授权管理】接受授权邀请"** 的**邀请授权**文案。
- **强结构性推断**：这是**企业实名 + 审核 + 邀请**模式，偏向创作者/服务商**分发**能力。
- **不可断言**：**B站是否提供全站视频关键词搜索，本次无法确认。** 若 Radar 需要全站搜索，在拿到文档前应视为**不满足**。
- **判定**：若需求可收窄到"我们已授权的自有/合作账号数据" → Conditional；若要全站搜索 → **No-Go（直到证伪）**。

#### 小红书（xiaohongshu）— No-Go（官方 API）/ Conditional（授权设备）
- **官方能力（已核验，决定性）**：原文 **"当前仅开放 basic_info 一个 scope。"** `user_profile` / `read_notes` / `write_notes` / `read_followers` **全部为"规划中"**。
- **明确禁区**：**不得把 OAuth 登录接口当成笔记搜索能力。** 全部已公布 scope 中**不存在任何"笔记搜索"**。
- **licensed 路径**：该官方页面**无任何授权内容供应商的记载**，**不得臆造**。
- **历史证据**：`m2-controlled-experiment.md` 显示匿名访问 `302 → /login`，出现 `登录` 与 `Forbidden` 标记 —— **公开网页路径有明确登录边界**。
- **唯一现实路径**：用户本人在手机上主动搜索并展示 → 经单独授权读取**最小截图**。
- **但这正是当前能力缺口所在**：没有 OCR/UI dump，截图**无法变成字段**（见第 2.3 节）。所以即使有 consent，**今天也只能产出"人工可看的截图"，不能产出结构化事件**。

#### 闲鱼（xianyu）— No-Go → Conditional
- **官方能力（已核验，决定性）**：
  - `alibaba.idle.isv.item.query`：**需要授权**，`item_id` **必填** → **按 ID 查单条**，**无关键词参数**；
  - `alibaba.idle.isv.spu.search`：**不需要授权**，`search_text` 可选 → 返回**"候选的品牌型号列表"**，用途是*"供服务商进行选择"*。
- **关键区分**：**两者都不是"全站二手商品关键词搜索"。** 不能把 `spu.search` 描述成闲鱼全站搜索。
- **附加约束**：两个接口均标注 **"聚石塔内调用"**（需在阿里内部云环境调用），这对接入架构有实质影响。
- **历史证据**：`previous_task_id: null` —— **无任何既有验证**。
- **判定**：官方路径当前 **No-Go**（无适用接口）；设备路径 Conditional，但依赖 consent 且受制于第 2.3 节缺口。

### 4.3 官方文档的实际抓取与核验（方法说明）

本任务要求"分别记录已发布的 API 和实际获批的 API 权限，不混淆"。本次对该维度做了**真实抓取**，方法如下：

**方法学发现（重要）**：本沙箱的 `web_fetch` 工具对**所有**域名都报 `resolves to a non-public IP address`，因为沙箱把 DNS 一律改写到 `198.18.0.0/15`（RFC 2544 保留测试网段）——实测连 `example.com`、`dns.google` 也如此。但**原始 `curl` 出口可达真实公网**。因此本次改用 `curl` 直取官方页面/接口，并获得**真实内容**（非摘要猜测）。

**独立复核**（本报告作者亲自重跑，非转述）：

| 目标 | URL | HTTP | 字节 | 结果 |
| --- | --- | --- | --- | --- |
| 知乎文档 JSON | `developer.zhihu.com/console/api/v3/docs` | 200 | 135,595 | ✅ 完整文档 |
| 小红书 scope | `openaccount.xiaohongshu.com/docs/scope` | 200 | 10,448 | ✅ 完整正文 |
| 闲鱼 `item.query` | `developer.alibaba.com/docs/api.htm?apiId=49488` | 200 | 170,270 | ✅ 完整正文 |
| 闲鱼 `spu.search` | `developer.alibaba.com/docs/api.htm?apiId=55300` | 200 | 47,963 | ✅ 完整正文 |
| 抖音视频搜索 | `developer.open-douyin.com/docs/.../aweme-dy-video-search` | 200 | 1,019,928 | ✅ 含正文 |
| B站文档 | `openhome.bilibili.com/doc` | 200 | 6,468 | ⚠️ **仅 SPA 空壳** |
| B站 scope API | `openhome.bilibili.com/arcopen/user/company-scope/scopelist` | 404 | — | ❌ 受鉴权保护 |

> **抓取到的原始响应仅存放于 `/tmp` 用于核验，未写入生产数据、未提交仓库。**

### 4.4 各平台官方文档的核验结论

#### 知乎 — ✅ 已核验（完整文档）
- **鉴权**：`Authorization: Bearer <your_access_secret>` + `X-Request-Timestamp`（**秒级** Unix 时间戳，服务端校验）。原文确认。
- **能力**：`zhihu_search`、`global_search`、`hot_list`、`question_answers`、`zhida`、知识库、创作能力、用户数据、PDF/PPT 工具。
- **额度**：`/api/v1/quota` 返回 `TotalQuota` / `TotalUsed` / `RemainingQuota`（**当前自然日**，最低为 0）。原文确认。
- **价格（已公开）**：知乎搜索 **20 元/千次**、全网搜索 **12 元/千次**、知乎热榜 **10 元/千次**、直答 Agent 20 元/千次、用户创作内容摘要 10 元/千次。免费额度原文标注 **"每日免费额度属于限时优惠，后续可能根据活动情况调整"**，北京时间 00:00 重置、**不结转**、同账号多 Access Secret **共用额度**。
- **错误码**：`20001` 鉴权失败、`30001` 频率超限、`90001` 额度读取失败。
- **结构性限制（对 Radar 很关键）**：`zhihu_search` 的 `Count` **最大 10**，超过自动截断；且响应字段 `HasMore` **"当前实现固定返回 `false`"** —— **意味着无法用该接口做深度翻页枚举**。这直接限制了"批量选题挖掘"的可行性。

#### 抖音 — ✅ 已核验（文档正文）
- **接口**：`GET https://open.douyin.com/dy_open_api/v1/search/video/`，**Scope `aweme.dy.video_search`**，**权限要求：抖音视频垂搜（对外）**。原文确认。
- **参数**：`keyword`、`device_id`（原文标注必填）、`count`、`cursor`、`open_id`、`publish_time`、`sort_type`、`search_id`。
- **鉴权**：请求头 `access-token`（`clt.` 前缀示例），经 `client_token` 或用户 OAuth 获取。
- **权限门控**：能力需在控制台**单独申请开通**，未获批会返回能力类错误码。
- ⚠️ **未核验细节（诚实降级）**：子代理称"审核 5–7 个工作日 / 提额另需 5–7 个工作日"，但本报告作者重抓 `.../openapi/list` 页（1,225,239 bytes）**未找到"工作日"字样**。该具体时限**未获证实，不作为结论引用**。可确认的只有"能力需申请开通"。

#### B站 — ⚠️ 未核验（但有结构性旁证）
- `openhome.bilibili.com/doc` 仅返回 6,468 字节 SPA 空壳；scope 接口 404（需鉴权）。
- **旁证（来自 JS bundle `app.ceab0096.js`，53,431 bytes）**：
  - 路由：`/arcopen/user/company-scope/{scopelist,submit}`、`/arcopen/user/company-core/*`、`/arcopen/user/webhooks/*`；
  - 原文文案：**"请完成身份信息填写，审核通过后即可创建应用，拥有稿件分发等开发者能力。"**
  - 原文文案：**"如果您是被授权用户，请前往【管理中心】-【授权管理】接受授权邀请"**。
- **可得出结论**：B站开放平台是**企业实名 + 审核 + 邀请授权**模式，定位是**稿件分发**等创作者/服务商能力。
- **不可得出结论**：**无法确认其是否提供全站视频关键词搜索**（文档正文取不到）。因此"官方仅覆盖授权 UP 主"应表述为**强结构性推断**，而非已证实事实。

#### 小红书 — ✅ 已核验（决定性）
`openaccount.xiaohongshu.com/docs/scope` 原文明确：**"当前仅开放 basic_info 一个 scope。"**

| Scope | 说明 | 状态 |
| --- | --- | --- |
| `basic_info` | 昵称、头像、gender、region | **已开放** |
| `user_profile` | 简介、fans、follows、notes | **规划中** |
| `read_notes` | 读取用户发布的笔记内容 | **规划中** |
| `write_notes` | 代表用户发布笔记 | **规划中** |
| `read_followers` | 关注/粉丝列表 | **规划中** |

- **结论**：**笔记内容读取能力对任何人都尚未开放**，且**全部 scope 列表中没有"笔记搜索"**。
- `write_notes` / `read_followers` 属**敏感权限**，需额外提交业务场景说明、隐私政策链接、数据使用承诺书。
- 该页**未**记载任何 licensed（授权内容供应商）路径 —— **不得臆造**。

#### 闲鱼 — ✅ 已核验（决定性，两个接口都查了）
经查 `apiId=49488`（`alibaba.idle.isv.item.query`）与 `apiId=55300`（`alibaba.idle.isv.spu.search`）：

| 接口 | 授权 | 关键参数 | 实际语义 |
| --- | --- | --- | --- |
| `alibaba.idle.isv.item.query` | **需要授权** | `item_id`（**必须**，即必填） | **按 item_id 单条查询**，返回单个商品详情（标题/价格/SKU/验货报告） |
| `alibaba.idle.isv.spu.search` | **不需要授权** | `search_text`（可选）、`channel_cat_id` | 返回**"候选的品牌型号列表"**（brand/model 字典） |

- 两者均标注 **"￥开放平台免费API"**，且均标注 **"聚石塔内调用"**（阿里内部云环境）。
- **结论**：`item.query` = **按 ID 查询**，**根本没有关键词参数**；`spu.search` = **品牌型号字典查询**，原文用途是 *"搜索的品牌和型号，供服务商进行选择"*（发布商品时的选择器）。
- **两者都不是闲鱼全站二手商品关键词搜索。** 本次**未发现任何**文档化的全站关键词搜索接口。
- ⚠️ **易错点**：`spu.search` 标"不需要授权"而 `item.query` 标"需要授权"，二者接入成本不同，不可混为一谈。

### 4.5 证据局限（诚实标注）

| 项 | 状态 |
| --- | --- |
| 五平台官方文档"能力"一列 | ✅ **知乎/抖音/小红书/闲鱼：已由真实页面核验**；B站：**未核验** |
| 五平台"我们是否已获批" | ❌ **全部 UNKNOWN** —— 无凭证、无控制台权限，**任何"已获批"声称都是伪报** |
| 抖音审核时限（5–7 工作日） | ❌ **未证实**，已从结论中移除 |
| B站是否有全站视频搜索 | ❌ **无法确认**（SPA + 鉴权门） |
| 小红书 licensed 供应商路径 | ❌ **官方页面无任何记载** |

### 4.6 与并行任务 SOC-API-001 的交叉一致性

并行任务产出 `docs/research/china-social/source-api-capability-20261013.md`（**非本任务产物**）。两份报告**独立得出**以下一致结论，互为佐证：

| 结论 | 本报告 | SOC-API-001 |
| --- | --- | --- |
| 五平台均未达 Authorized / Live | ✅ 一致（全部 UNKNOWN） | ✅ 一致 |
| 小红书官方仅 `basic_info`，不满足笔记搜索 | ✅ 一致 | ✅ 一致 |
| 闲鱼 `item.query` 非全站搜索 | ✅ 一致 | ✅ 一致 |
| 知乎为首要候选 | ✅ 一致 | ✅ 一致 |
| 安卓 P2 须先获用户设备授权、先入 staging | ✅ 一致 | ✅ 一致 |

**差异（互补，非冲突）**：SOC-API-001 记录了 B站 `Scope ARC_BASE` 与授权稿件查询文档链接；本报告未能取到 B站文档正文，仅有 JS bundle 旁证。**两者结合**可提高"B站为授权创作者模式"的可信度，但**仍不足以断言其是否提供全站搜索** —— 该点保持未核验。

> **注**：SOC-API-001 报告称其先前 MiMo 任务因 `model_empty_response` 失败，故由其"独立只读证据调研补齐"。本报告如实转述该声明，**不对其完成度背书**。

---

## 5. 最小字段映射

### 5.1 目标契约：`core.RawEvent`

`../content-opportunity-radar/core.py` L65–92 定义：

| RawEvent 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | 通常由 `stable_id()`（L32）生成 |
| `provider` | str | 提供方 |
| `source` | str | 来源 |
| `acquisition_method` | `AcquisitionMethod` | **关键**，见 5.2 |
| `retrieved_at` | datetime | 必填 |
| `external_id` / `url` / `title` / `text` | str? | 主体内容 |
| `author` / `community` | str? | 归属 |
| `published_at` | datetime? | 发布时间 |
| `language` / `country` | str? | `zh` / `CN` |
| `metrics` | dict[str,float] | 数值指标 |
| `provenance` | `Provenance` | `terms_class` / `api_version` / `endpoint` |

**问题所在**：`Provenance`（L58–62）**没有 `consent_scope` 字段**，`RawEvent` 也**没有 `dedupe_key` 字段**。而 `tasks/china-social-source-expansion-20261012.json` 的 P1 退出标准明确要求统一 RawEvent 包含 `platform、source_id、captured_at、provenance、consent_scope、dedupe_key`。

> **契约缺口 C**：现有 `RawEvent` **无法承载** P1 要求的 `consent_scope` 与 `dedupe_key`。这是一个**已存在的、需要单独变更**的架构差距，**不在本次任务范围**（本任务只读，不改业务代码）。

### 5.2 `AcquisitionMethod` 没有"设备屏幕"枚举值

`core.py` L47–56 的全部取值：

```
OFFICIAL_API, OAUTH_API, RSS, ATOM, PUBLIC_WEB_API, HTML, SEARCH_PROVIDER, LICENSED
```

**没有任何一个值能诚实描述"用户在本人手机上展示的屏幕证据"。**

若强行用 `HTML` 或 `PUBLIC_WEB_API` 承载截图证据，就是**在字段层面伪造来源** —— 这正是任务要禁止的"假装 RawEvent"。

> **契约缺口 D**：必须新增独立的采集路径表述（例如 `DEVICE_SCREEN_CONSENT`），**且该变更属于架构变更**，按 `AGENTS.md` 应走 Supervisor 决策，不能由 worker 顺手加。

### 5.3 最小字段映射表（仅对"官方 API"路径成立）

| 目标字段 | 知乎（示例） | 说明 |
| --- | --- | --- |
| `provider` | `zhihu` | 平台标识 |
| `source` | `zhihu_search` | 端点派生 |
| `acquisition_method` | `OFFICIAL_API` | 官方接口 |
| `retrieved_at` | 请求完成时间（UTC） | 必须是真实时间 |
| `external_id` | 问题/回答 ID | 来自响应 |
| `url` | 规范化 URL | **必须剥离 query/fragment** |
| `title` | 标题 | ≤240 字符 |
| `metrics` | 仅 `social.py` `SocialMetric` 白名单内的指标 | 见 5.4 |
| `provenance.terms_class` | `official` | 分类 |
| `provenance.endpoint` | `/api/v1/content/zhihu_search` | **已核验的真实端点**，审计用 |

**知乎侧已核验的请求要素**（来自 2026-10-13 抓取）：
- 端点：`GET https://developer.zhihu.com/api/v1/content/zhihu_search`；
- 请求头：`Authorization: Bearer <your_access_secret>` + `X-Request-Timestamp: <秒级 Unix 时间戳>`（服务端校验）；
- 参数：`Query`（必填）、`Count`（**最大 10**，超出自动截断）；
- 响应：`Data.Items[]` + `Data.HasMore`（**固定 `false`**）+ `Data.SearchHashId`；
- 额度自查：`GET /api/v1/quota?APIIDs=...`。

**映射注意**：`SearchHashId` 属请求标识、非内容标识，**不应**作为 `external_id`；`external_id` 应取条目自身 ID。

### 5.4 指标白名单（`social.py` L20–23）

允许：`mention_count, new_content_count, engagement, engagement_velocity, creator_count, creator_diversity, question_count, pain_count, comparison_count, content_supply, content_velocity, search_rank, hot_rank`。

**记录**：该白名单**没有** `download_count`、`rating`、`install_count` 等应用商店指标 —— 所以历史那张应用商店截图里的"102 亿次下载""4.2 分"**在当前契约下无处安放**（也本就不该作为社媒信号）。

### 5.5 屏幕证据的映射：**不存在**

对安卓屏幕路径，**当前不存在合法的 RawEvent 映射**。可行的只有**暂存区封装**（第 7 节）。

---

## 6. 来源保真 / 脱敏 / 撤权清理

### 6.1 来源保真（Source Fidelity）

现有缺口（第 2.3 节）：report **无 `captured_at`、无 `sha256`、无前台包名**。

**必须补齐的三件套**（P2 前置条件）：

| 需补字段 | 作用 | 现状 |
| --- | --- | --- |
| `captured_at`（设备侧 UTC） | 证明截图时点 | ❌ 缺 |
| `screenshot_sha256` | 原始证据完整性 | ❌ 缺 |
| `foreground_package` | 证明截图**确实来自目标平台** | ❌ 缺 |

**没有 `foreground_package`，就无法排除"用户展示的是 A 平台、却声称是 B 平台"的错配。** 这是保真的核心。

### 6.2 脱敏（Redaction）—— 已有基础设施

`collector.py` 提供了**现成的**脱敏层：

- `SECRET_METADATA_KEYS`（L54–67）：`access_token`、`cookie`、`password`、`secret` 等；
- `_sensitive_key()`（L70–92）：含 `token`/`secret`/`cookie`/`credential` 等子串即命中；
- `_sanitize_for_storage()`（L121–134）：**递归**把命中字段替换为 `[REDACTED]`。

**但注意其局限**：它只处理**结构化字段名**。**截图像素里的隐私（通知栏、账号名）它完全管不到。**

> 因此屏幕路径的脱敏必须是**图像级**的，且**默认最小化**：只保留目标区域的裁剪图，而不是整屏。这只是设计约束，尚未实现。

### 6.3 撤权清理（Revocation）

| 要求 | 现状 | 缺口 |
| --- | --- | --- |
| 授权带过期时间 | `source_intake.SourceGrant.expires_at`（L46）+ `validate()` L57 检查过期 | ✅ 已有 |
| 授权可禁用 | `SourceGrant.enabled`（L48） | ✅ 已有 |
| 撤权后可定位并删除事件 | `dedupe_key` 稳定可定位（L137–139） | ⚠️ **无撤销 API** |
| 原始截图可删除 | artifact 文件在 workspace 内 | ⚠️ **无清理流程** |

**缺口 E**：缺少"用户撤权 → 找出该 `grant_ref` 的全部暂存行与截图 → 物理删除 → 留审计记录"的流程。P2 前必须定义。

---

## 7. 屏幕证据如何进入暂存区（而不是假装 RawEvent）

**好消息：这个机制已经存在。** `../content-opportunity-radar/source_intake.py`（当前**未提交**，`git status` 显示 `??`，创建于 2026-10-13 12:04，疑似并行任务 SOC-SCHEMA-001 产出）。

### 7.1 关键设计（逐条对应源码）

| 设计点 | 源码位置 | 说明 |
| --- | --- | --- |
| 独立于 RawEvent 的 pathway | L18 `PATHWAYS` 含 **`android_screen`** | 屏幕路径是一等公民，但**不是** `AcquisitionMethod` |
| **显式标记非生产事件** | L150 `"is_production_raw_event": False` | **核心防伪造机制** |
| 状态只能是暂存 | L150 `"status": "staged_only"` | 不叫 completed |
| 屏幕路径免于费用闸门 | L59 `if self.pathway != "android_screen"` | 设备路径不涉及 API 费用 |
| **但仍需有效授权** | L55–58 `validate()` | 必须 `enabled` 且**未过期** |
| 输入字段白名单 | L32–34 `INPUT_FIELDS` | 只收 6 个字段 |
| **拒绝设备标识** | `test_source_intake.py` L84 显式断言 `device_id` 被拒 | 设备序列号**不得**入库 |
| **拒绝原始响应/凭据** | 同上，`cookie`/`access_token`/`raw_html`/`private_message` 全被拒 | 防泄漏 |
| URL 剥离 query/fragment | L94–95 `urlunsplit(..., "", "")` | 防跟踪参数入库 |
| 域名白名单 | L24–30 `HOSTS` | 只收五个平台的官方域 |
| 稳定去重键 | L137–139 `sha256(platform + pathway + source_id)` | **跨平台不会错误去重** |
| 证据哈希 | L146–148 `evidence_sha256` | 内容变更可检出 |

### 7.2 验证结果（本次实跑）

以 `python3.12` 运行只读测试，**11 项全部通过**：

```
Ran 11 tests in 0.001s
OK
```

其中直接相关的一项：

```
test_android_screen_requires_user_grant_and_remains_staging ... ok
```

该用例（`test_source_intake.py` L73–81）同时验证了：
1. `android_screen` pathway 可被接受；
2. 结果 `is_production_raw_event == False`；
3. **授权被禁用时必须抛 `SourceGateError`**。

### 7.3 完整的"屏幕 → 暂存"流水线（设计，未实现采集侧）

```
手机（用户主动展示）
      │  adb exec-out screencap -p        ← 已实现（android_runtime.py L97）
      ▼
全屏 PNG（含隐私）
      │  图像级脱敏 + 区域裁剪              ← ❌ 未实现（缺口 F）
      ▼
最小截图 + foreground_package 校验         ← ❌ 未实现（缺口 A'/第6.1节）
      │  人工/OCR 提取少量字段               ← ❌ 未实现（缺口 G）
      ▼
accept_candidate(candidate, grant)         ← ✅ 已实现（source_intake.py L98）
      │  校验授权、剥离 URL、算 hash/去重
      ▼
staged_only 行（is_production_raw_event=False）  ← ✅ 已实现
      │  人工审查 + 单独架构变更
      ▼
（未来）RawEvent —— 需要新增 AcquisitionMethod 枚举值
```

> **这条流水线的中间三段（裁剪脱敏、前台包校验、字段提取）完全不存在。** 所以当前状态是：**两端已就绪，中间断裂。** 这正是 P2 的工作量所在，也是**今天不能宣布安卓采集可用**的原因。

### 7.4 与 `collector.py` 的关系

`source_intake.py` 只产出**暂存 dict**，**不写** `raw_events` 表。`collector.py::_upsert_event()`（L395）才是写库入口，且其 schema（L294–380）没有 `is_production_raw_event` 或 `consent` 列。

**因此两条链路是隔离的** —— 这是正确的：截图像素**不可能**悄悄流进生产 `raw_events` 表。

---

## 8. 截图真实采集的权限边界

### 8.1 允许（仅当全部满足）

1. ✅ 设备为**用户本人所有**；
2. ✅ 用户**当面/明确**点击确认 USB 调试授权；
3. ✅ 用户**自己**在手机上完成搜索、翻页、打开帖子（**不由系统代劳**）；
4. ✅ 每次采集**有明确意图与时限**，事后可撤权；
5. ✅ 只读：仅 `getprop` + `screencap`；
6. ✅ 产出标记 `staged_only`，且 `is_production_raw_event=False`。

### 8.2 禁止（红线）

| 禁止项 | 依据 |
| --- | --- |
| ❌ 模拟登录 / 注入 Cookie / 读取 Access Token | `implementation_rules` 第 3 条 |
| ❌ 读取 App 私有数据库、短信、聊天记录 | 同上 |
| ❌ 绕过验证码、反爬、DRM、风控 | 同上 |
| ❌ 自动点击、自动滑动、批量模拟操作 | 任务 prompt |
| ❌ 把截图当作官方 API 事件的替代品 | 第 5.2 节契约缺口 D |
| ❌ 伪造"已采集成功" | 任务 prompt："若证据无法读取，报告 blocked，不可伪报完成" |
| ❌ 修改业务代码 / Docker / 付费 Worker | 任务 prompt |

### 8.3 边界推论

> **在用户明确授权之前，唯一允许的动作是"读取本地工具状态"（`adb devices -l`）。**
> 本报告严格遵守了该边界：**未执行任何 `shell` / `screencap` / `getprop`**。

---

## 9. 真机手动验收步骤（P2，待用户执行）

> **前置：以下全部步骤需用户本人在场并明确同意。当前状态 —— 尚无安卓真机授权，P2 待验证。**

### 阶段 0：授权准备（用户在手机上完成）
1. 手机「设置 → 关于手机 → 连续点击版本号 7 次」开启开发者模式；
2. 「开发者选项 → USB 调试」打开；
3. USB 连接电脑，手机弹出**「允许 USB 调试吗？」** → 用户**亲自**点「允许」（建议勾选"一律允许"以便复现）；
4. 用户口头或书面确认：**同意本次只读采集**，并指定**平台**与**时间窗**。

### 阶段 1：主机侧确认（只读）
```bash
export ADB_BIN=/opt/homebrew/bin/adb
"$ADB_BIN" devices -l
```
**预期**：出现 `e2dec9dd  device ...`（若显示 `unauthorized` → 回到步骤 3）。

### 阶段 2：本地运行只读 worker（不触发平台请求）
```bash
cd /path/to/ai-team-skills/ai-team-orchestrator
python3.12 android-worker/android_runtime.py \
  --request-file  <一个含 inspect_device 的 request.json> \
  --artifact-manifest <manifest.jsonl>
```
**验收点**：`android_report.json` 中 `mode == "read_only"`、`screenshot_available == true`。

### 阶段 3：用户在手机上展示目标页面
5. **用户自己**打开目标 App，**自己**搜索关键词，**自己**停在目标结果页；
6. 用户示意"可以拍了" → 执行 `screencap`（**仅此一次**，不翻页、不点击）。

### 阶段 4：一致性核对（人工）
7. 打开 PNG，人工确认：**截图内容 == 用户声称的平台与搜索词**；
8. **记录** `foreground_package`（当前需人工记录，代码未实现 —— 缺口 A'）；
9. 计算 `sha256` 并记录（当前需人工，代码未实现）。

### 阶段 5：安全进入暂存区
10. 从截图**只抄录最小字段**（如标题、公开互动数），**不抄写**通知栏/账号信息；
11. 用 `source_intake.accept_candidate()` 生成暂存行；
12. **断言** `status == "staged_only"` 且 `is_production_raw_event == False`。

### 阶段 6：撤权与清理（验收后必做）
13. 用户在手机上「开发者选项 → 撤销 USB 调试授权」；
14. 按 `grant_ref` 删除本次暂存行与截图，保留一条审计记录（**缺口 E**，需先定义流程）。

### 失败判据（必须如实记录）
- 无设备 / `unauthorized` → **blocked_by_device**；
- 截图 < 100 bytes 或非 PNG → **失败**；
- 无法确认截图平台归属 → **不得进入暂存区**；
- 任一红线动作被要求 → **停止并上报**。

---

## 10. Go / Conditional / No-Go 判定

### 10.1 分路径判定

| 路径 | 判定 | 理由 |
| --- | --- | --- |
| **知乎官方 API** | 🟢 **Go（取得凭证后）** | 文档完整、价格公开、端点实测可达；缺 Access Secret。**唯一现实的首选路径** |
| **抖音官方 API** | 🟡 **Conditional Go** | 有真实关键词搜索接口；但能力需控制台审批，资质未核 |
| **B站官方 API** | 🟡 **Conditional Go（收窄）** | 文档未取到；旁证示企业审核+邀请授权；**全站搜索无法确认** |
| **小红书官方 API** | 🔴 **No-Go** | 已核验：仅 `basic_info` 开放，`read_notes` 规划中，**无笔记搜索** |
| **闲鱼官方 API** | 🔴 **No-Go** | 已核验：`item.query`=按ID查、`spu.search`=品牌型号字典，**均非关键词搜索** |
| **五平台安卓屏幕路径** | 🟡 **Conditional（P2，待验证）** | 需用户授权 + 补齐缺口 A'/F/G；**尚无真机授权** |
| **安卓屏幕 → RawEvent** | 🔴 **No-Go（当前）** | `AcquisitionMethod` 无对应枚举；须架构变更 |

### 10.2 任务整体判定

> ## 🟡 Conditional Go
> **官方 API 方向：知乎在有凭证后可直接推进（五平台中唯一"绿灯"）；抖音/B站需先解决资质。小红书与闲鱼的官方 API 路径已被证据否决。安卓屏幕方向在当前代码下不可交付，P2 因"尚无真机授权"而 blocked_by_device。**

### 10.3 触发重新判定的条件

转为 **Go** 需同时满足：
1. 知乎取得**真实 Access Secret** 并通过小限额验证（含确认 `HasMore=false` 下的枚举策略）；
2. P2 缺口 A'/F/G 补齐（前台包校验、图像脱敏、字段提取）；
3. `AcquisitionMethod` 完成架构变更评审；
4. 用户本人完成设备授权。

转为 **No-Go** 若：抖音资质不符 **且** B站确认无全站搜索 **且** 用户拒绝设备授权。**小红书与闲鱼的官方 API 路径已判为 No-Go，无需再等。**

---

## 11. 缺口清单（Gap Register）

| ID | 缺口 | 位置 | 阻塞对象 | 严重度 |
| --- | --- | --- | --- | --- |
| **A** | `ADB_BIN` 未在普通 shell 提供 | `android_runtime.py` L36–40 | 手工运维 | 中 |
| **A'** | 无 `foreground_package`，无法证明截图归属平台 | `android_runtime.py` L83–93 | **P2 保真** | **高** |
| **B** | E2E `state.json` 终态 `passed` 后 watcher 永久停推 | `android_e2e_watcher.py` L130–131 | 重跑需 `--reset` | 中（真相安全） |
| **C** | `RawEvent`/`Provenance` 无 `consent_scope`、`dedupe_key` | `core.py` L58–92 | P1 退出标准 | 高 |
| **D** | `AcquisitionMethod` 无设备屏幕枚举 | `core.py` L47–56 | **防伪造核心** | **高** |
| **E** | 无撤权 → 定位删除 → 审计流程 | 全局 | 合规 | 高 |
| **F** | 无图像级脱敏/区域裁剪 | 全局 | 隐私 | **高** |
| **G** | 无 OCR / UI 节点提取 | 全局 | **P2 可用性** | **高** |
| **H** | report 无 `captured_at` / `sha256` | `android_runtime.py` L83–93 | 保真与审计 | 中 |
| **I** | B站官方文档正文不可达（SPA + 鉴权） | 外部站点 | B站路径判定 | 中（已标注未核验） |
| **J** | 知乎 `HasMore` 固定 `false`，无法深翻页 | 平台侧结构性限制 | Radar 枚举设计 | 中（需 P1 应对） |

---

## 12. 明确不做的事（本次遵守声明）

- ✅ **未调用任何真实平台 API**（未携带任何凭证发起业务请求；仅 `curl` 读取**公开文档页面**，零凭证、零业务数据）；
- ✅ 未模拟登录、未输入、未点击、未绕过验证码；
- ✅ 未爬取任何私有数据库；
- ✅ 未对设备执行 `shell` / `screencap` / `getprop`（仅主机侧 `adb devices -l`）；
- ✅ 未修改业务代码、生产数据、Docker、Grok、火山、付费 Worker 设置；
- ✅ 唯一新增文件即本报告。

**本次实际执行的命令（全部只读）**：
`adb version`、`adb devices -l`、`python3.12 -m unittest test_android_worker`、`python3.12 -m unittest test_source_intake`、PNG 头解析、源码检索，以及 `curl` 读取**公开文档页**（知乎文档 JSON、小红书 scope、闲鱼两个 API 页、抖音视频搜索页、B站 doc 页与其 JS bundle）。
抓取到的响应仅存于 `/tmp` 用于核验，**未写入生产数据、未提交仓库**。

---

## 13. 给 Supervisor 的交接摘要

1. **安卓 worker 真实能力很窄**：只有设备信息 + 全屏截图；**无 OCR、无 UI 节点、无来源识别**。
2. **历史真机 E2E 是真实通过的**（`task_c45eaaf30064672952e2`，OnePlus PJX110，1080×2376 PNG），但那是**设备能力验收**，不是**平台采集**。
3. **设备当前在线且已授权**（`e2dec9dd`），但**本任务未获授权碰设备**，且 watcher 已停推（终态 `passed`）。
4. **暂存区机制已存在且测试通过**（`source_intake.py`，`android_screen` pathway，`is_production_raw_event=False`）—— 这是防止"假装 RawEvent"的现成闸门。
5. **但中间三段断裂**：图像脱敏、前台包校验、字段提取全部缺失 → **今天无法产出任何平台事件**。
6. **官方 API 已做真实抓取核验**（`curl` 直取，非摘要猜测）：
   - **知乎 = 唯一绿灯**（文档完整 + 价格公开 + 额度自查接口）；但 `HasMore` 固定 `false`，**枚举策略需重设计**；
   - **小红书 = 证据否决**（仅 `basic_info`，`read_notes` 规划中，无笔记搜索）；
   - **闲鱼 = 证据否决**（`item.query` 按 ID 查、`spu.search` 品牌型号字典，**均非全站搜索**）；
   - **抖音 = 有接口但需审批**；**B站 = 文档未取到，不可断言**。
7. **判定：Conditional Go**；**P2 = blocked_by_device**。
8. **所有"我们是否已获批"均为 UNKNOWN** —— 无凭证、无控制台权限，不得声称已获批。
9. **建议下一步**：
   - 推进**知乎 Access Secret** 申请（ROI 最高）；
   - 同批确认抖音资质、B站文档（需带会话的浏览器）；
   - 补缺口 A' / F / G；**小红书/闲鱼暂停官方 API 投入**。

---

**报告状态**：decision-quality，可供 Supervisor 使用。
**证据基础**：源码逐文件审查 + 本地只读工具查询 + `curl` 直取官方文档（知乎/抖音/小红书/闲鱼已核验）。
**证据局限**：B站文档正文不可达（SPA + 鉴权）→ 已标注未核验；五平台"是否已获批"全部 UNKNOWN。
**未验证声明**：尚无安卓真机授权，P2 待验证。
