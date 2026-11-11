# 工作区前端只读审查报告（WORKBUDDY_WORKSPACE_REVIEW）

- 审查对象：Content Opportunity Radar — 工作区前端（新建 / 编辑 / 切换工作区）的易用性与多语言一致性
- 审查类型：**只读审查**。未修改、未删除、未提交、未推送任何项目源码或数据
- 审查文件：`dashboard/index.html`、`dashboard/app.js`、`dashboard/styles.css`、`dashboard/i18n.js`
  （为确认接口契约，另只读参考了 `api.py`、`radar_product.py`、`workspace.py`、`read_model.py`）
- 审查日期：2026-10-08
- 交接依据：`AI_TEAM_AGENT_CONTEXT.md`、`WORKBUDDY_HANDOFF.md`

---

## 0. 被审查版本（必须固定，否则结论会漂移）

**重要：审查期间有并发写入。** `dashboard/app.js` 在本轮审查开始的同一分钟（19:05）被其他 Worker 修改过（新增 `#scope-input` 的 `readOnly` 与提交回填逻辑）。为得到可复验的结论，本报告固定在下列快照上，并在审查结束时复核哈希未再变化。

| 文件 | SHA-256 | 快照时间 |
| --- | --- | --- |
| `dashboard/app.js`（854 行） | `33a7f4f4540a0e84166fb363a282f9f6976faa2697348ab7aeac0c284726a447` | 2026-10-08 19:05 |
| `dashboard/i18n.js`（208 行） | `040096358ca0e21bfea6c4baf57eb57e988639276a4b4053a7d25e464c98c18a` | 2026-10-08 17:44 |
| `dashboard/index.html`（205 行） | `63bfc816b43f62eb648b69c0984eaa817d452b7d6c6344a6006243de7ffc4ceb` | 2026-10-08 17:44 |
| `dashboard/styles.css`（1229 行） | `be73e14d72ad35aa23d00e4361e7c2a37ee460fc217a4849f34eae3f75296f49` | 2026-10-08 17:42 |

- 复核时间 19:44：四个文件哈希与上表**完全一致**，审查期间无二次变动。
- 冻结副本与运行环境均置于 `/tmp/radar-review/`，**未写入项目目录**（仅 Python 导入可能刷新既有 `__pycache__/*.pyc`，属构建缓存，非源码）。
- 行号引用均对应上表版本。若后续 Worker 再改 `app.js`，请先比对哈希。

---

## 1. 验证方法（严格区分两种证据）

| 证据类型 | 本报告中的标记 | 说明 |
| --- | --- | --- |
| 实际运行验证 | **【已验证·运行】** | 用真实 Chromium 驱动真实页面 + 真实 HTTP 接口，可截图/可读数值/可看网络面板 |
| 代码阅读推断 | **【已验证·代码】** | 逻辑与接口契约可确定，但未构造运行环境（例如需要外部条件） |
| 未验证推测 | **【推测】** | 尚未证实，需 Supervisor 决定是否补验 |

具体做法：

1. **静态校验（【已验证·运行】）**
   - `node --check dashboard/app.js`、`node --check dashboard/i18n.js` → 通过。
   - 用 Node `vm` 加载 `i18n.js`，把 8 个语包与 `index.html` 的 `data-i18n` 键做集合比对。
   - 结果：8 语包**各 107 个键**、键集合完全一致、**无空值**；`index.html` 使用的 **40 个键全部命中**。
   - 另有 7 个键与英文完全同形，逐个核对后确认属正确译法（`Name`/`Citations`/`Questions`/`Research Pack`/`Error`/`snapshots`/`Workspace`），**不是缺陷**。
2. **单元测试（【已验证·运行】，在冻结副本内运行，不写项目目录）**
   - `python3 -m unittest test_dashboard_i18n test_workspace_actions -v` → **Ran 4 tests, OK**。
3. **真实运行环境**
   - 数据库：**临时库** `/tmp/radar-review/runtime/radar.db`（新初始化），绝不触碰项目 `.radar/radar.db`。
   - 服务：`radar_product.py --db <临时库> --port 8799`（冻结副本 `/tmp/radar-review/app`，内含 19:05 版本 dashboard）。
   - 浏览器：`agent-browser`（Chromium），headless；视口按需切换；用 `eval` 读 DOM/计算样式/几何，用 `network requests` 读真实请求。
4. **运行校验与单测的关系**：单测全绿**只说明字典完整与接口可用**，无法覆盖本报告的 V1/V2/V3/V4/V5/V6 —— 这些必须靠浏览器验收。两类证据不混淆。

---

## 2. 结论摘要

| 编号 | 严重性 | 问题 | 证据 |
| --- | --- | --- | --- |
| V1 | **高** | 首屏不加载工作区列表，刷新后已有工作区**彻底无法选中**，且无任何入口可恢复 | 运行 |
| V2 | 中 | 切回「全部历史数据（未分类）」时沿用上一个工作区的**范围与语言**，标签与真实数据范围不符（误导） | 运行 |
| V3 | 中 | 新建/编辑失败的提示**未本地化**，并直接向用户暴露内部 ID | 运行 |
| V4 | 中 | 编辑「语言不在下拉选项内」的工作区时，语言框空白 → **保存必然失败** | 运行 |
| V5 | 中低 | 工作区切换的语言一致性有两处缺陷（声明语言不生效 / 覆写全局语言偏好） | 运行 |
| V6 | 中低 | 弹窗内容偏高时，**动作按钮落在可视区外**，无固定页脚，只能靠弹窗内滚动触达 | 运行 |
| V7 | 低 | 工作区模式下「雷达范围」输入框只读但**无任何视觉提示** | 运行 |
| V8 | 低 | **同名工作区**在列表中无法区分 | 运行 |
| V9 | 低 | 新建成功后**重复拉取**工作区列表（2 次 `/v1/workspaces`） | 运行 |
| V10 | 低 | 工作区列表请求失败时**静默清空**列表并静默降级 | 代码 |
| V11 | 信息性 | 图省事的外部 `capture.js` 被自身 CSP 拦截等 4 项观察 | 运行/代码 |

**一句话结论**：字典与后端接口是健康的（单测全绿、i18n 8 语齐全），问题集中在前端的**状态初始化（V1）**与**工作区上下文切换的状态收敛（V2/V4/V5）**；V1 是唯一会阻断主流程的缺陷，建议优先修。

---

## 3. 已验证问题明细

### V1（高）首屏不加载工作区列表，刷新后已有工作区无法选中

- **位置**：`dashboard/app.js` L840–L851 初始化 IIFE 与 L854 `loadDashboard();` —— 二者均未调用 `loadWorkspaces()`；`loadWorkspaces()` 定义在 L620，只被两处调用：L707（新建/编辑成功后）、L745（下拉框 `change` 处理器内部）。
- **现象与证据（【已验证·运行】）**：
  - 服务端真值：`GET /v1/workspaces` 返回 **6 个工作区**（更早一次为 11 个，结论不变）。
  - 页面真值：首屏 `#workspace-select` 只有 **1 个 option**：`全部历史数据（未分类）`；`#workspace-edit` 为 `disabled`。
  - 网络真值：`reload` 后页面只发出 `GET /v1/opportunities?scope=AI&limit=20` 与 `GET /v1/providers`，**完全没有 `/v1/workspaces` 请求**。
  - 死锁性：下拉框只有 1 项时，用户**无法产生 `change` 事件**（重选同一项不触发），因此没有任何用户可达的入口把列表加载出来；唯一"自救"方式是再新建一个工作区（L707 才会调 `loadWorkspaces`），或手工触发事件。
  - 叠加因素：`state.workspaceId` 不做任何持久化（无 localStorage、无 URL 参数），刷新即回到未分类，于是"刷新即丢失选择、且无法重新选择"。
- **复现步骤**：
  1. 启动产品端（本报告用 `radar_product.py --db <临时库> --port 8799`）。
  2. `POST /v1/lite/workspaces/create` 建 1 个工作区；`GET /v1/workspaces` 确认返回 1 条。
  3. 浏览器打开 `http://127.0.0.1:8799/`（或对已打开页面刷新）。
  4. 观察「工作区」下拉框：只有「全部历史数据（未分类）」；「编辑」按钮灰显。
  5. DevTools → Network 过滤 `workspaces`：无请求。
- **影响**：主流程阻断。用户会认为"刚建的工作区丢了"，并可能重复创建（触发 V3 的报错），体验上等价于数据丢失。
- **建议修改**（交 Supervisor 派工，本次不改）：
  1. 初始化时 `await loadWorkspaces();` 再 `loadDashboard();`。
  2. 把选中工作区持久化（建议 URL query，如 `?workspace_id=...`，便于分享与刷新保留；或 localStorage），启动时校验其仍存在，不存在再回落。
  3. `loadWorkspaces()` 内避免重复拉取（见 V9）。
- **需要的测试**：
  - 单测/端到端：预置 1 个工作区 → 打开页面 → 断言下拉项数 == 2、`#workspace-edit` 可用、且发出过 `/v1/workspaces` 请求。
  - 回归：预置 2 个工作区 → 选中第 2 个 → 刷新 → 断言仍选中第 2 个（若采纳持久化）。

---

### V2（中）切回「全部历史数据（未分类）」沿用上一个工作区的范围与语言，标签与真实语义不符

- **位置**：`app.js` L717–L748。回填 `state.scope` / `$("scope-input")` / 语言只发生在 `if (workspace) { ... }`（L725–L740）内部；选中 legacy（`value=""`）时 `workspace` 为 `undefined`，**不做任何重置**。
- **后端语义（【已验证·代码】）**：`read_model.py` L490–L497，`workspace_id is None` 时 `where = "scope = ? AND COALESCE(workspace_id, '') = ''"` —— 即「**该 scope 下、未归属任何工作区**的记录」。所以该模式既不是"全部历史"，也仍受 scope 过滤。
- **现象与证据（【已验证·运行】）**：
  - 先切到工作区「具身智能研究」（zh）→ 再切回「全部历史数据（未分类）」。
  - 结果：`#scope-input` 仍为 `具身智能`；`#run-summary` 显示「范围"具身智能"暂无持久化 Radar 结果」；**实际请求**为 `GET /v1/opportunities?scope=%E5%85%B7%E8%BA%AB%E6%99%BA%E8%83%BD&limit=20`。
  - 另一次实测（切到 ja 工作区后再切回）：界面语言保持 `ja`，下拉标签显示「すべての履歴（未分類）」而请求仍是上一个工作区的 scope。
- **复现步骤**：
  1. 让工作区列表加载出来（见 V1 复现第 1–2 步，或先建一个新工作区）。
  2. 选择任一工作区（如「具身智能研究」）→ 等列表刷新。
  3. 把下拉框切回「全部历史数据（未分类）」。
  4. 观察 `#scope-input` 的值、`#run-summary` 文案，并在 Network 看实际请求的 `scope` 参数。
- **影响**：**这正是任务点名的"未分类历史数据误导"**。用户以为在看"全部历史"，实际看的是"上一个工作区主题下、未归工作区的记录"，两者差异很大；空结果时更容易误判为"没有数据"。
- **建议修改**：
  1. legacy 分支显式收敛状态：scope 回到"用户偏好默认值"或上次手工输入值（需先定义语义），语言回到用户偏好而非上一个工作区的语言。
  2. 文案与真实语义对齐：将「全部历史数据（未分类）」改为不产生歧义的表达（例如「未归入工作区（当前范围：X）」），或提供一个真正不做 scope 过滤的入口。
- **需要的测试**：切换 `工作区 → legacy` 后断言 `#scope-input` 与请求参数一致、且等于预期默认值；断言界面语言回到用户偏好。

---

### V3（中）新建/编辑失败的提示未本地化，并暴露内部 ID

- **位置**：`app.js` L709–L711，`error.textContent = err.message;` —— 直接展示服务端原文，**未经过 `I18N`**。
- **现象与证据（【已验证·运行】）**：
  - 中文界面下重复新建同名+同主题工作区 → 弹窗提示：`workspace already exists: ws_dc07a15ca22bea1b3881012d`（服务端 `api.py` 400 原文，`workspace.py` L295–L296 构造）。
  - 编辑一个 `language=it` 的工作区并保存 → 弹窗提示：`language is required`（服务端 400）。
  - 该提示节点 `#workspace-editor-error` 的 `lang` 属性为空。
- **复现步骤**：
  1. 建一个工作区（名称 N、主题 S）。
  2. 再次点「新建」，填同样的 N 与 S，点「保存工作区」。
  3. 观察弹窗内红色提示：为英文原文，且含 `ws_...` 内部 ID。
- **影响**：中文/日文用户看到英文报错；内部 ID 无意义且暴露实现细节；与页面上已本地化的全局错误（`showError(tf(...))`）风格不一致。
- **建议修改**：按错误类型映射 i18n 键（至少覆盖 `workspaceExists`、`languageRequired`、`nameRequired`、`scopeRequired`、`notFound`），只展示可读文案，`ws_...` 只在 console 记录；同时给提示节点设置当前 `lang`。
- **需要的测试**：对上述 400 分支各写一条断言，校验弹窗文本等于目标语言的字典值；并加一条"界面语言为 zh 时不得出现英文原文"的守护断言。

---

### V4（中）编辑「语言不在下拉选项内」的工作区时语言框空白，保存必然失败

- **位置**：`app.js` L682 `$("workspace-language").value = existing?.language || state.language;`；`index.html` L77–L82 的下拉只列 8 种语言；而服务端 `radar_product.py` 的 `_language()` 允许 `[A-Za-z]{2,3}`，即 `it`、`ru`、`nl` 等都能存进库（CLI `workspace.py create --language it` 与 API 均可）。
- **现象与证据（【已验证·运行】）**：
  - 库中存在 `Italian Test`（`language=it`）。下拉框选中它 → 点「编辑」。
  - 实测弹窗状态：`langValue: ""`、`langIndex: -1`（**没有任何选项被选中**）。
  - 直接点「保存工作区」→ 请求 `POST /v1/lite/workspaces/update` 返回 **400**，弹窗提示 `language is required`，保存失败，弹窗保持打开。
- **复现步骤**：
  1. 造一个非 8 语的工作区：`python3 workspace.py --db <库> create --name "Italian Test" --scope testing --language it --country IT`（或直接 POST `/v1/lite/workspaces/create`）。
  2. 打开页面并让工作区列表加载出来（见 V1）。
  3. 在下拉框选择 `Italian Test` → 点「编辑」。
  4. 观察「语言」下拉框为空白；点「保存工作区」→ 报 `language is required`。
- **影响**：这类工作区**完全无法编辑**（连改名字/主题都会一并失败），因为提交体会带上空 `language`。
- **建议修改**（择一）：
  1. 打开编辑弹窗时，若 `existing.language` 不在选项内，动态插入一个同名 option 并选中，保证原值可保留；
  2. 或让语言选项与服务端允许的枚举同源（服务端收敛白名单），并在创建时就拒绝白名单外的语言。
- **需要的测试**：`language=it` 的工作区走一遍"编辑→仅改名字→保存成功"，断言 `language` 字段未被改写。

---

### V5（中低）工作区切换的多语言一致性缺陷

任务点名"工作区切换引起语言变化不一致"，实测有两处：

**V5a：声明语言不在选项内时，静默沿用上一个工作区的语言（【已验证·运行】）**

- 位置：`app.js` L728–L739，仅当 `if (option)` 命中才应用语言；否则**什么都不做，也不提示**。
- 证据：从 `Robot Weekly`（`language=ja`）切到 `Italian Test`（`language=it`）后：`#language-select` 仍为 `ja`、`document.documentElement.lang` 仍 `ja-JP`、界面文案仍为日文（「新規」「ワークスペースを編集」），而工作区声明的是 `it`。
- 影响：工作区语言与界面语言静默错配；用户无法判断"为什么切了工作区界面没变"。
- 建议：命中不到时按 `I18N.normalize()` 兜底（其对未知语言会回落 `en`）并可选提示，避免无反馈。

**V5b：切换工作区会覆写全局语言偏好（【已验证·运行】+【已验证·代码】）**

- 位置：`app.js` L737 调用 `applyInterfaceLanguage()`，而 L46 会执行 `localStorage.setItem("radarLanguage", state.language)`。
- 证据（运行）：先新建一个 `language=en` 的工作区（提交后界面变英文），随后**刷新页面**：浏览器 `navigator.language` 为 `zh-CN`，但页面 `documentElement.lang` 变成 `en-US` —— 说明用户手工选择的界面语言被工作区语言写进了全局偏好。
- 影响：用户"选工作区"这一动作，会永久改变下次打开时的默认界面语言，属于跨会话的隐性副作用。
- 建议：工作区语言只作用于视图层，不要写回全局偏好；全局偏好只应由顶部语言下拉框的 `change` 写入。
- **需要的测试**：`setItem` 的调用点收窄后，补一条"切换工作区不得改变 localStorage 偏好"的断言；以及"未知语言回落 en 且有反馈"的断言。

---

### V6（中低）弹窗内容偏高时动作按钮落在可视区外（无固定页脚）

- **位置**：`dashboard/styles.css` L197–L205 `.workspace-dialog` **未声明** `max-height` / `overflow`；`.workspace-dialog-actions`（L219）为普通流式布局，**非 sticky**。
- **重要澄清（避免误报）**：Chromium 的 UA 样式为 `dialog:modal` 提供了 `max-height: calc(100% - 38px)` 与 `overflow: auto`，所以内容**可以被内部滚动触达，不会永久裁切**。缺的是项目自己的显式约束与"主操作常驻可见"。
- **现象与证据（【已验证·运行】）**：
  - 新建弹窗内容高度约 **405–410px**（390px 宽实测 `scrollHeight=405`；756px 宽含错误行时 446）。
  - 视口 **390×380**：弹窗盒体 `[top=19, bottom=361]`，`scrollHeight 405 > clientHeight 340`（溢出 65px）；动作按钮 rect `[355, 401]` —— **既在弹窗下沿之外，也在视口（380）之外**，`submitInViewport = false`。
  - 把 `dialog.scrollTop` 滚到 65 后：按钮回到 `[290, 336]`，可见 → 说明只是"出界"，不是"不可达"。
  - 视口 **320×260**：溢出 242px，按钮 rect `[339, 385]`，远在视口外；截图确认标题被顶出、只见名称/主题/语言三行。
  - 视口 **390×480**：不溢出（`scrollHeight == clientHeight`），按钮可见 —— 阈值约为**视口高度 ≈ 445px**（405 内容 + UA 的 38px 余量）。
- **复现步骤**：
  1. 浏览器打开页面，把视口高度调到 ≤ 400px（如 390×380）。
  2. 点「新建」。
  3. 观察弹窗下沿之外的动作按钮「取消 / 保存工作区」；把鼠标移到弹窗内滚动才可见。
- **影响**：小窗/横向手机/带软键盘的场景下，用户第一眼找不到"保存"，会误以为弹窗坏了；错误提示出现后内容更长，情况更明显。
- **建议修改**：
  1. 给 `.workspace-dialog` 显式加 `max-height: min(90dvh, 640px); overflow: auto;`（不再依赖 UA 默认）。
  2. `.workspace-dialog-actions { position: sticky; bottom: 0; background: var(--surface); }`，保证主操作常驻可见。
  3. 顺带补 `[readonly]` 视觉样式（见 V7）。
- **需要的测试**：浏览器验收脚本在 390×380、320×568 两个视口断言"打开弹窗后提交按钮的 `getBoundingClientRect()` 完全落在视口内"。

---

### V7（低）工作区模式下「雷达范围」输入框只读但无视觉提示

- **位置**：`app.js` L643–L644（`loadWorkspaces` 内）与 L720–L721（`change` 内）设置 `readOnly = Boolean(state.workspaceId)` 与 `title`；`styles.css` 中 **grep 不到任何 `readonly` 规则**（已实测无匹配）。
- **现象与证据（【已验证·运行】）**：工作区模式下 `readOnly: true`，但计算样式为 `opacity: 1`、`cursor: text`、文字色与普通态完全一致、背景透明 —— 与可编辑状态**视觉无差别**；只有鼠标悬停才出现 `title` 提示（且提示文案复用 `editWorkspace`，如日文「ワークスペースを編集」）。
- **影响**：用户尝试输入范围却毫无反应，会判断为"输入框坏了"。
- **建议修改**：加 `[readonly]` 样式（降低不透明度 / `cursor: not-allowed` / 淡色背景），并在字段旁放一句静态说明（如「范围由工作区决定，点『编辑』修改」）；`title` 文案建议独立成 i18n 键，而不是复用"编辑工作区"。
- **需要的测试**：断言只读态与可编辑态的 `opacity`/`cursor` 计算值不同（防止样式回退）。

---

### V8（低）同名工作区在列表中无法区分

- **现象与证据（【已验证·运行】）**：服务端两个不同 `workspace_id` 的工作区同名「同名测试」（`scope` 分别为 `alpha` / `beta`）。下拉框实际渲染为两项，**label 都是「同名测试」**，肉眼无法区分。
- **影响**：选中错误工作区；结合"没有删除入口"，误建记录会长期污染列表。
- **建议修改**：option 文本附带主题，例如 `名称 · 主题`；或对重名给出提示。若要根治，需定义唯一性规则（当前 `workspace_id` 由 `名称+主题` 派生，允许同名）。
- **需要的测试**：造两个同名工作区，断言两个 option 的可见文本互不相同。

---

### V9（低）新建成功后重复拉取工作区列表

- **位置**：`app.js` L707 `await loadWorkspaces();` 紧接 L708 派发 `change`，而 `change` 处理器 L745 内**又** `await loadWorkspaces();`。
- **现象与证据（【已验证·运行】）**：一次新建的网络序列为 `POST /v1/lite/workspaces/create 200` → `GET /v1/workspaces 200` → `GET /v1/workspaces 200` → `GET /v1/workspaces/{id}/opportunities?limit=20 200`（两次列表拉取）。
- **建议修改**：`change` 处理器内改为"仅在需要时刷新"，或让新建路径只派发 `change` 而不自行 `loadWorkspaces`。
- **需要的测试**：断言一次新建流程中 `/v1/workspaces` 的请求次数为 1。

---

### V10（低）工作区列表请求失败时静默清空并静默降级【已验证·代码】

- **位置**：`app.js` L621 `api("/v1/workspaces").catch(() => ({ items: [] }))`；L637–L639 当前 id 不在列表时静默 `state.workspaceId = ""`。
- **影响**：接口异常（或工作区被 `disable`）时，下拉框静默变空、编辑按钮静默变灰，用户完全不知道发生了什么，也没有重试入口。
- **建议修改**：区分"确实为空"与"请求失败"；失败时保留旧列表并在顶部给出可重试的错误提示（`showError` + i18n）。
- **需要的测试**：mock `/v1/workspaces` 返回 500，断言列表不清空且出现本地化错误提示。

---

## 4. 推测问题（尚未验证，请 Supervisor 决定是否补验）

| 编号 | 推测内容 | 为什么未验证 | 风险 |
| --- | --- | --- | --- |
| S-A | `#workspace-editor-error` 初始为 `hidden`，显示时"取消隐藏 + 写入文本"同帧发生，部分屏幕阅读器可能不播报（`role="alert"` 已在，属好实践） | 未做读屏软件实测 | 低 |
| S-B | V6 的"可滚动兜底"来自 Chromium 的 UA 样式；Safari/WebKit 或旧 WebView 的 `dialog:modal` 默认值可能不同，**在非 Chromium 内核下可能升级为真正的裁切** | 仅 Chromium 实测 | 中（若产品要跑非 Chromium WebView 需补验） |
| S-C | 弹窗标题 `<h3 data-i18n="newWorkspace">` 是静态键，编辑态由 JS 覆写成"编辑工作区"；若将来语言切换能在 modal 打开时触发（或去掉 modal），`applyStatic` 会把标题退回"新建工作区" | 当前 modal 使页面 inert，用户不可达 | 低（潜在） |
| S-D | `#scope-input` 只读时表单仍可提交，`scope-form` 的 submit 会用 `workspace.scope` 回填（`app.js` L752–L755，19:05 并发新增）。当前行为正确；但若将来移除 `readOnly`，该回填会变成**静默覆盖用户输入**的隐患 | 需先决定该交互的最终设计 | 低 |

---

## 5. 正向结论（这些方面未发现问题）

1. **多语言字典本身是完整的**：8 语包各 107 键、键集合一致、无空值；`index.html` 的 40 个 `data-i18n` 键全部命中；`node --check` 双文件通过；`test_dashboard_i18n.py` 3 项全过。
   - 真实运行亦验证：切到 `language=ja` 的工作区后，界面文案、`html.lang`、日期格式全部切到日文，静态与动态文案均正确。
2. **工作区 CRUD 主链路可用**：UI 新建成功后，下拉框补齐全部工作区并**自动选中新建项**、`#workspace-edit` 变可用、范围回填并进入只读态、语言随之切换（实测 `create → 200`、`selected = 新建 id`、`optionCount = 12`）。
3. **编辑链路可用**：编辑「Robot Weekly」并改主题为 `robotics-v2`，保存后弹窗关闭、下拉选中项不变、范围同步为 `robotics-v2`，服务端已落库（`POST /v1/lite/workspaces/update 200`）。
4. **前后端契约一致**：`workspace_id` / `name` / `scope` / `language` / `country` 字段名与 `Workspace.to_dict()` 完全对应；服务端 `create/update/list` 单测通过（`test_workspace_actions.py`）。
5. **安全面未发现新问题（顺带观察）**：写操作限 loopback（`api.py` `_local_action_allowed`）；静态资源带 CSP 与 `X-Content-Type-Options`；工作区与历史数据隔离正确（`read_model` 对 `workspace_id is None` 显式排除已归属记录）。**本报告不构成安全审计结论**，仅记录观察。

---

## 6. 其他观察（信息性，非缺陷）

1. `index.html` L203 引入 `https://mcp.figma.com/mcp/html-to-design/capture.js`，被服务端自身 CSP（`script-src 'self'`）拦截 —— 网络面板显示该请求无状态码。属**无效外部依赖**（既无功能，也把第三方域写进了页面），建议移除或与 CSP 决策对齐。
2. `i18n.js` 中 `en.legacyWorkspace` 的基础值 `"Legacy / unscoped"`（L4）会被 L181–L195 的 `workspaceLabels` 覆盖为 `"All history (uncategorized)"`，属**死值**；不影响运行，但容易在后续改动中引发困惑。
3. 工作区能力**缺少删除/停用入口**：误建或重名记录只能长期留在下拉框（本报告为验证 V8 造的两条同名记录即无法从 UI 清理）。若"保留旧数据"是产品原则，建议至少提供 `enabled=false` 的停用入口。
4. `RADAR_LITE_USER_GUIDE.md` 等文档未覆盖工作区操作，建议在 V1/V2 修复后同步补一段用户指引。

---

## 7. 修复优先级建议（供 Supervisor 派工参考）

1. **P0**：V1（阻断主流程，且修法简单、风险低）——建议独立小 PR，附带端到端断言。
2. **P1**：V4 + V3（V4 让一类工作区无法编辑；V3 是可直接观察到的体验缺陷，两者都在"弹窗错误"这条路径上，可一起做）。
3. **P1**：V2（涉及语义定义，需要先确认"未分类"到底应该显示什么，再动代码）。
4. **P2**：V5 / V6 / V7 / V9，以及 V8、V10。
5. 若采纳 V1 的 URL 持久化方案，注意与现有 `workspaceQuery()`（`app.js` L544–L548）参数拼接方式保持一致。

---

## 8. 交接说明与边界

- 本次为**只读审查**：未修改任何源码、未删除/覆盖任何文件、未执行 git 提交或推送，也未写入项目 `.radar/radar.db`（全部运行验证都使用 `/tmp/radar-review/` 下的临时库与冻结副本）。
- 报告中的每一项"【已验证·运行】"均可按第 3 节的复现步骤独立复验；复验时请以第 0 节的 SHA-256 为准，若 `app.js` 哈希已变，请先确认 V1–V10 是否仍然成立。
- 任何改代码需求（含第 7 节优先级建议）**交回 Supervisor 统一派工**，本报告不直接落地实现。
- 附：运行期证据（截图/探针脚本/服务日志）位于 `/tmp/radar-review/`（如 `final-390x380-error.png`、`vp3-320x260-error.png`、`verify*.sh`、`server.log`），属临时文件，未纳入项目目录；如需长久留存请另指派归档。
