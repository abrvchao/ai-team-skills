# Radar 持续追踪 V2 技术方案

> 版本：v2-design-draft · 日期：2026-10-08
> 约束：不修改现有 V1 代码，不触碰 DSH / WorkBuddy 配置，不破坏历史数据。

---

## 1. 目标与边界

### 1.1 目标

在 Radar Lite V1（工作区 + 手动 scan + 只读 API）基础上，增加**持续追踪**能力：

| 能力 | 说明 |
|------|------|
| 定时扫描 | 工作区级 cron 调度，自动触发 `run_discovery` |
| 变化比较 | 相邻两次 run 之间检测新机会、消失机会、排名跃迁、分数漂移 |
| 提醒 | 变化达到阈值时生成结构化 alert，供下游消费者（Dashboard / AI Team / 通知渠道）订阅 |
| 可验收合同 | 每次追踪产出确定性、可回放的 diff artifact，人工或自动化均可验收 |

### 1.2 非目标（显式排除）

- **不替换 V1 数据模型**：`radar_runs` / `opportunity_snapshots` / `radar_evidence` 表结构不变。
- **不引入新 Provider**：数据源调度复用 V1 `Collector` + `workspace_collection_plan`。
- **不做 LLM 推理**：变化比较和提醒完全确定性，不依赖模型。
- **不触碰 DSH / WorkBuddy**：本方案仅在 `content-opportunity-radar/` 目录内新增文件。
- **不修改 API V1 路由**：V2 新增独立路由前缀 `/v2/`，V1 路由行为不变。

---

## 2. V1 架构回顾（只读，不改）

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Workspace    │────▶│ Collector    │────▶│ run_discovery()  │
│ Store        │     │ (job_state)  │     │  ├ seed collect  │
│ (SQLite)     │     │              │     │  ├ candidates    │
└─────────────┘     └──────────────┘     │  ├ deep scan     │
                                          │  └ ranking       │
                                          └────────┬────────┘
                                                   │
                                          ┌────────▼────────┐
                                          │ OpportunityRead  │
                                          │ Store (SQLite)   │
                                          │ ├ radar_runs     │
                                          │ ├ opportunity_   │
                                          │ │  snapshots     │
                                          │ └ radar_evidence │
                                          └────────┬────────┘
                                                   │
                                          ┌────────▼────────┐
                                          │ API V1 (HTTP)    │
                                          │ Read-only        │
                                          └─────────────────┘
```

关键不变量（V2 必须保持）：
- `opportunity_snapshots` 是 **append-only**；同一 `snapshot_id` 不会被覆盖。
- `radar_runs.run_id` 由 `stable_id("radar-run", workspace_id, scope, generated_at, ...)` 生成，确定性且幂等。
- V1 `list_opportunities()` 始终返回最新 run 的结果；历史通过 `opportunity_history()` 查询。

---

## 3. V2 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Radar V2 新增层                           │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Tracker       │  │ Change       │  │ Alert            │  │
│  │ Scheduler     │──│ Detector     │──│ Dispatcher       │  │
│  │ (cron)        │  │ (diff)       │  │ (结构化通知)      │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                    │             │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌───────▼──────────┐  │
│  │ tracker_runs │  │ tracker_     │  │ tracker_alerts   │  │
│  │ (SQLite)     │  │ diffs        │  │ (SQLite)         │  │
│  │              │  │ (SQLite)     │  │                  │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Contract Verifier (验收合同生成 + 校验)                │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│              V1 层（不修改）                                  │
│  WorkspaceStore · OpportunityReadStore · Collector · API V1  │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 数据模型扩展（新增表，不改旧表）

所有 V2 表位于同一 SQLite 数据库（`.radar/radar.db`），通过独立 migration 创建，不影响 V1 schema。

### 4.1 `tracker_schedules` — 调度配置

```sql
CREATE TABLE IF NOT EXISTS tracker_schedules (
    schedule_id TEXT PRIMARY KEY,          -- stable_id("tracker-sched", workspace_id)
    workspace_id TEXT NOT NULL,
    cron_expr TEXT NOT NULL DEFAULT "0 */6 * * *",  -- 默认每 6 小时
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_schedules_due
ON tracker_schedules(next_due_at ASC, enabled DESC)
WHERE enabled = 1;
```

### 4.2 `tracker_runs` — 追踪执行记录

```sql
CREATE TABLE IF NOT EXISTS tracker_runs (
    tracker_run_id TEXT PRIMARY KEY,       -- stable_id("tracker-run", workspace_id, scheduled_at)
    schedule_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    radar_run_id TEXT,                     -- 关联 V1 radar_runs.run_id
    scheduled_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',  -- running / completed / failed
    error_message TEXT,
    prev_radar_run_id TEXT,                -- 上一次成功的 radar_run_id，用于 diff
    FOREIGN KEY(schedule_id) REFERENCES tracker_schedules(schedule_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_runs_workspace_time
ON tracker_runs(workspace_id, scheduled_at DESC, tracker_run_id DESC);
```

### 4.3 `tracker_diffs` — 变化检测结果

```sql
CREATE TABLE IF NOT EXISTS tracker_diffs (
    diff_id TEXT PRIMARY KEY,              -- stable_id("tracker-diff", prev_run_id, curr_run_id, topic_id, change_type)
    tracker_run_id TEXT NOT NULL,
    prev_radar_run_id TEXT,
    curr_radar_run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    change_type TEXT NOT NULL,             -- new / disappeared / rank_up / rank_down / score_up / score_down
    prev_rank INTEGER,
    curr_rank INTEGER,
    prev_score REAL,
    curr_score REAL,
    magnitude REAL NOT NULL DEFAULT 0.0,   -- 变化幅度（0-100），用于排序和阈值过滤
    details_json TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL,
    FOREIGN KEY(tracker_run_id) REFERENCES tracker_runs(tracker_run_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_diffs_run
ON tracker_diffs(tracker_run_id, magnitude DESC, change_type);

CREATE INDEX IF NOT EXISTS idx_tracker_diffs_topic
ON tracker_diffs(topic_id, detected_at DESC);
```

### 4.4 `tracker_alerts` — 提醒记录

```sql
CREATE TABLE IF NOT EXISTS tracker_alerts (
    alert_id TEXT PRIMARY KEY,             -- stable_id("tracker-alert", diff_id)
    diff_id TEXT NOT NULL,
    tracker_run_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,              -- 同 change_type
    severity TEXT NOT NULL DEFAULT 'info', -- info / warning / critical
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(diff_id) REFERENCES tracker_diffs(diff_id)
);

CREATE INDEX IF NOT EXISTS idx_tracker_alerts_workspace
ON tracker_alerts(workspace_id, created_at DESC, delivered);
```

---

## 5. 核心模块设计

### 5.1 `tracker.py` — 调度器

```python
# 新增文件：tracker.py
# 职责：
#   1. 管理 tracker_schedules 的 CRUD
#   2. 扫描到期 schedule，触发 run_discovery
#   3. 将结果写入 tracker_runs
#   4. 调用 change_detector 生成 diff
#   5. 调用 alert_dispatcher 发送提醒

class TrackerStore:
    """V2 tracker 持久层，与 V1 OpportunityReadStore 同库不同表。"""
    
    def create_schedule(self, workspace_id, cron_expr="0 */6 * * *") -> dict
    def update_schedule(self, schedule_id, *, cron_expr=None, enabled=None) -> dict
    def list_schedules(self, *, enabled_only=False) -> list[dict]
    def due_schedules(self, now=None) -> list[dict]
    
    def start_run(self, schedule_id, workspace_id, scheduled_at) -> str  # tracker_run_id
    def complete_run(self, tracker_run_id, *, radar_run_id, prev_radar_run_id) -> None
    def fail_run(self, tracker_run_id, *, error_message) -> None

class TrackerScheduler:
    """定时调度入口。"""
    
    def tick(self, *, now=None) -> list[dict]:
        """
        1. 查询 due_schedules()
        2. 对每个到期 schedule：
           a. start_run() 记录开始
           b. 调用 workspace.run_workspace() （V1 已有）
           c. 获取 prev_radar_run_id（上一次成功的 run）
           d. 调用 ChangeDetector.detect()
           e. 调用 AlertDispatcher.dispatch()
           f. complete_run() 或 fail_run()
        3. 返回本轮所有 tracker_run 摘要
        """
    
    def run_loop(self, *, poll_seconds=60) -> None:
        """持续运行的 daemon 入口；适合 Docker / systemd 部署。"""
```

**调度策略**：
- 默认 `0 */6 * * *`（每 6 小时），工作区可自定义。
- 最小间隔 1 小时，防止 API 配额耗尽。
- 使用 `next_due_at` 字段做乐观锁，多进程安全。
- 如果 V1 collector 的 `job_state` 显示所有 provider 均 stale，跳过本轮 scan 并记录 warning。

### 5.2 `change_detector.py` — 变化检测

```python
# 新增文件：change_detector.py
# 职责：比较同一 workspace 相邻两次 radar run 的 opportunity_snapshots

class ChangeDetector:
    """确定性 diff 引擎。不依赖 LLM，不引入随机性。"""
    
    # 变化类型常量
    NEW = "new"                    # 上次不在 top-N，本次进入
    DISAPPEARED = "disappeared"    # 上次在 top-N，本次不在
    RANK_UP = "rank_up"           # 排名上升 ≥ 2 位
    RANK_DOWN = "rank_down"       # 排名下降 ≥ 2 位
    SCORE_UP = "score_up"         # opportunity_score 上升 ≥ 5.0
    SCORE_DOWN = "score_down"     # opportunity_score 下降 ≥ 5.0
    
    def __init__(
        self,
        store: OpportunityReadStore,
        tracker_store: TrackerStore,
        *,
        rank_threshold: int = 2,       # 排名变化阈值
        score_threshold: float = 5.0,  # 分数变化阈值
        top_n: int = 20,               # 比较前 N 名
    ) -> None: ...
    
    def detect(
        self,
        *,
        workspace_id: str,
        prev_run_id: str,
        curr_run_id: str,
        scope: str,
    ) -> list[dict]:
        """
        返回 diff 列表，每项包含：
        {
            "diff_id": str,
            "topic_id": str,
            "topic_name": str,
            "change_type": str,
            "prev_rank": int | None,
            "curr_rank": int | None,
            "prev_score": float | None,
            "curr_score": float | None,
            "magnitude": float,         # 归一化变化幅度
            "details": dict,
        }
        
        算法：
        1. 从 opportunity_snapshots 读取两次 run 的 top-N 数据
        2. 构建 topic_id → row 映射
        3. 检测 new：curr 有、prev 无
        4. 检测 disappeared：prev 有、curr 无
        5. 检测 rank_up/down：|prev_rank - curr_rank| >= rank_threshold
        6. 检测 score_up/down：|prev_score - curr_score| >= score_threshold
        7. 计算 magnitude = max(rank_delta / top_n * 50, score_delta)
        8. 按 magnitude 降序排列
        """
    
    def persist_diffs(self, diffs: list[dict], *, tracker_run_id: str, 
                      prev_run_id: str, curr_run_id: str, workspace_id: str) -> None:
        """将 diff 写入 tracker_diffs 表。"""
```

**设计要点**：
- `top_n=20` 而非 5：V1 默认 top 5 太少，变化检测需要更大窗口才有意义。
- `magnitude` 用于排序和过滤：只有 magnitude ≥ 10 的 diff 才会触发 alert。
- 同一 topic 可能产生多种 change_type（如同时 rank_up + score_up），分别记录。
- 如果 prev_run_id 为 None（首次运行），所有 top-N 机会标记为 `new`，magnitude=0。

### 5.3 `alert_dispatcher.py` — 提醒分发

```python
# 新增文件：alert_dispatcher.py
# 职责：将 diff 转化为结构化 alert，支持多种投递渠道

class AlertRule:
    """单条提醒规则。"""
    change_types: list[str]           # 关注哪些 change_type
    min_magnitude: float = 10.0       # 最低变化幅度
    severity: str = "info"            # info / warning / critical
    channels: list[str]               # file / webhook / stdout
    
class AlertDispatcher:
    """确定性 alert 分发器。"""
    
    def __init__(self, tracker_store: TrackerStore, rules: list[AlertRule]) -> None: ...
    
    def dispatch(self, diffs: list[dict], *, tracker_run_id: str, 
                 workspace_id: str) -> list[dict]:
        """
        1. 按 rule 过滤 diffs
        2. 生成 alert 记录（title + summary）
        3. 写入 tracker_alerts 表
        4. 按 channel 投递
        5. 更新 delivered / delivered_at
        """
    
    def render_alert(self, diff: dict, rule: AlertRule) -> dict:
        """
        生成人类可读的 alert：
        {
            "alert_id": str,
            "alert_type": str,
            "severity": str,
            "title": "🟢 新机会: AI Agent Evaluation Framework",
            "summary": "workspace 'AI Safety' 本轮扫描发现新机会...",
            "topic_id": str,
            "magnitude": float,
            "radar_url": "/v1/opportunities/{topic_id}?workspace_id=...",
        }
        """
```

**投递渠道**（V2 Lite 范围）：

| 渠道 | 说明 | 优先级 |
|------|------|--------|
| `file` | 写入 `.radar/lite/alerts/{workspace_id}.jsonl` | P0 必做 |
| `stdout` | 打印到 tracker daemon 日志 | P0 必做 |
| `webhook` | POST JSON 到配置的 URL | P1 可选 |
| `email` | 不在本方案范围 | 排除 |

**Alert 去重**：
- 同一 `diff_id` 只产生一条 alert（`alert_id = stable_id("tracker-alert", diff_id)`）。
- 如果同一 topic 连续多轮出现相同 change_type，只在首次告警，后续降为 `info`。

### 5.4 `contract_verifier.py` — 验收合同

```python
# 新增文件：contract_verifier.py
# 职责：为每次追踪运行生成可验证的验收合同

class ContractVerifier:
    """
    验收合同是确定性、可回放的 JSON 文档，描述一次 tracker run 的完整行为。
    人工审阅或自动化测试均可消费。
    """
    
    def verify(self, *, tracker_run_id: str, workspace_id: str) -> dict:
        """
        生成验收合同：
        {
            "schema_version": "tracker-contract-v1",
            "tracker_run_id": str,
            "workspace_id": str,
            "radar_run_id": str,
            "prev_radar_run_id": str | None,
            "contract_checks": [
                {
                    "check": "radar_run_completed",
                    "passed": True,
                    "detail": "radar run abc123 completed at 2026-10-08T12:00:00Z"
                },
                {
                    "check": "diff_deterministic",
                    "passed": True,
                    "detail": "replaying diff with same inputs produces identical output"
                },
                {
                    "check": "no_data_loss",
                    "passed": True,
                    "detail": "V1 opportunity_snapshots count unchanged: 42 rows"
                },
                {
                    "check": "alert_consistency",
                    "passed": True,
                    "detail": "3 diffs → 2 alerts (1 filtered by magnitude < 10)"
                },
                {
                    "check": "history_intact",
                    "passed": True,
                    "detail": "opportunity_history for topic:xyz has 5 entries, unchanged"
                }
            ],
            "diff_summary": {
                "total_diffs": 5,
                "new": 1,
                "disappeared": 0,
                "rank_up": 2,
                "rank_down": 1,
                "score_up": 1,
                "score_down": 0
            },
            "alert_summary": {
                "total_alerts": 2,
                "delivered": 2,
                "by_severity": {"warning": 1, "info": 1}
            },
            "verified_at": "2026-10-08T12:01:00Z"
        }
        """
    
    def replay_diff(self, *, prev_run_id: str, curr_run_id: str, 
                    workspace_id: str, scope: str) -> list[dict]:
        """
        重放 diff：用相同输入重新运行 ChangeDetector.detect()，
        验证结果与已存储的 tracker_diffs 完全一致。
        用于自动化验收测试。
        """
```

**合同检查项**：

| 检查 | 说明 |
|------|------|
| `radar_run_completed` | V1 radar run 成功完成 |
| `diff_deterministic` | 相同输入产生相同 diff（重放验证） |
| `no_data_loss` | V1 表行数未减少（append-only 不变量） |
| `alert_consistency` | alert 数量 ≤ diff 数量，过滤规则一致 |
| `history_intact` | 每个 topic 的 opportunity_history 长度 ≥ V2 运行前 |
| `schedule_progress` | next_due_at 已推进到下一个 cron 时间点 |

---

## 6. API V2 路由（新增，不改 V1）

所有 V2 路由挂在 `/v2/tracker/` 前缀下。V1 路由 `/v1/` 完全不变。

```
GET  /v2/tracker/schedules                    # 列出所有调度
POST /v2/tracker/schedules                    # 创建调度 {workspace_id, cron_expr}
PATCH /v2/tracker/schedules/{schedule_id}     # 更新调度 {cron_expr, enabled}

GET  /v2/tracker/runs                         # 列出追踪运行记录
GET  /v2/tracker/runs/{tracker_run_id}        # 单次运行详情

GET  /v2/tracker/runs/{tracker_run_id}/diffs  # 该次运行的变化列表
GET  /v2/tracker/diffs?topic_id=xxx           # 按 topic 查历史变化

GET  /v2/tracker/alerts                       # 列出提醒
POST /v2/tracker/alerts/{alert_id}/ack        # 确认提醒

POST /v2/tracker/tick                         # 手动触发一次 tick（调试用）
GET  /v2/tracker/runs/{tracker_run_id}/contract  # 获取验收合同
```

**权限**：与 V1 一致，所有 POST/PATCH 仅限 localhost。

---

## 7. 部署与运行模式

### 7.1 模式 A：Daemon（推荐）

```bash
# 新增入口：tracker_daemon.py
python tracker_daemon.py --db .radar/radar.db --poll-seconds 60
```

- 持续运行，每 60 秒检查到期 schedule。
- 适合 Docker / systemd / launchd 部署。
- 日志输出到 stdout，结构化 JSON。

### 7.2 模式 B：Cron 触发

```bash
# 每小时检查一次到期 schedule
0 * * * * cd /path/to/radar && python tracker_daemon.py --once --db .radar/radar.db
```

- `--once` 模式：执行一轮 tick 后退出。
- 适合已有 cron 基础设施的环境。

### 7.3 模式 C：手动触发

```bash
# 通过 API 手动触发
curl -X POST http://127.0.0.1:8787/v2/tracker/tick
```

- 适合调试和验收测试。

---

## 8. 与 V1 的交互契约

### 8.1 读取 V1 数据（只读）

| V2 模块 | 读取的 V1 数据 | 方式 |
|---------|---------------|------|
| TrackerScheduler | WorkspaceStore.get() | 获取 workspace 配置 |
| TrackerScheduler | run_workspace() / run_discovery() | 触发 V1 scan |
| ChangeDetector | OpportunityReadStore.list_opportunities() | 读取两次 run 的 top-N |
| ContractVerifier | OpportunityReadStore.opportunity_history() | 验证历史完整性 |

### 8.2 不修改 V1 数据

- V2 **不写入** `radar_runs` / `opportunity_snapshots` / `radar_evidence`。
- V2 通过 V1 的 `record_discovery()` 间接写入（这是 V1 已有流程）。
- V2 自身只写入 `tracker_*` 表。

### 8.3 向后兼容

- V1 API 路由不受影响。
- V1 Dashboard 不需要修改即可继续工作。
- V2 新增的表在 V1 数据库中可以安全存在（`CREATE TABLE IF NOT EXISTS`）。
- 如果 V2 未启用（没有 tracker_schedules 记录），V1 行为完全不变。

---

## 9. 文件清单

仅新增文件，不修改任何现有文件：

| 文件 | 职责 |
|------|------|
| `tracker.py` | TrackerStore + TrackerScheduler |
| `change_detector.py` | ChangeDetector diff 引擎 |
| `alert_dispatcher.py` | AlertRule + AlertDispatcher |
| `contract_verifier.py` | ContractVerifier 验收合同 |
| `tracker_daemon.py` | Daemon / cron / 手动触发入口 |
| `tracker_api.py` | V2 HTTP 路由注册 |
| `test_tracker.py` | 调度器单元测试 |
| `test_change_detector.py` | diff 引擎单元测试 |
| `test_alert_dispatcher.py` | 提醒分发单元测试 |
| `test_contract_verifier.py` | 验收合同单元测试 |
| `RADAR_V2_DESIGN.md` | 本文档 |

---

## 10. 验收标准

### 10.1 功能验收

| # | 验收条件 | 验证方式 |
|---|---------|---------|
| 1 | 创建 schedule 后，到期时自动触发 run_discovery | `test_tracker.py` |
| 2 | 首次运行后，所有 top-N 机会产生 `new` diff | `test_change_detector.py` |
| 3 | 第二次运行后，正确检测 rank_up / rank_down / score_up / score_down / disappeared | `test_change_detector.py` |
| 4 | magnitude < 10 的 diff 不触发 alert | `test_alert_dispatcher.py` |
| 5 | alert 写入 `.radar/lite/alerts/` JSONL 文件 | `test_alert_dispatcher.py` |
| 6 | 验收合同所有 checks passed | `test_contract_verifier.py` |
| 7 | replay_diff 结果与首次 detect 结果完全一致 | `test_contract_verifier.py` |
| 8 | V1 API `/v1/opportunities` 返回不变 | 现有 `test_read_api.py` 通过 |
| 9 | V1 `opportunity_snapshots` 行数不减少 | `test_contract_verifier.py` |
| 10 | 手动 tick 触发后产生正确的 tracker_run 记录 | `test_tracker.py` |

### 10.2 回归验收

```bash
# V1 测试不受影响
cd /path/to/content-opportunity-radar
/opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py'

# V2 新增测试
/opt/homebrew/bin/python3 -m unittest test_tracker test_change_detector test_alert_dispatcher test_contract_verifier
```

---

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 频繁 scan 耗尽 API 配额 | Provider 被限流 | 最小间隔 1 小时；检查 collector job_state |
| diff 误报（排名小幅波动触发 alert） | 用户疲劳 | magnitude 阈值 + 连续相同类型去重 |
| SQLite 并发写入冲突 | tracker_run 状态不一致 | WAL 模式 + 乐观锁（next_due_at） |
| V1 schema 变更导致 V2 读取失败 | diff 引擎崩溃 | V2 只通过 V1 公开方法读取，不直接 SQL 查 V1 表 |
| Daemon 进程意外退出 | 漏跑 schedule | 启动时扫描 overdue schedule，补跑最近一轮 |

---

## 12. 未来扩展（不在本方案范围）

- **多 workspace 并行 scan**：当前串行，V2.1 可引入线程池。
- **Dashboard V2 面板**：展示 diff 时间线和 alert 历史。
- **Webhook / Slack / 邮件通知**：AlertDispatcher 增加 channel 实现。
- **跨 workspace 关联**：检测同一 topic 在不同 workspace 的表现差异。
- **LLM 辅助摘要**：对 diff 集合生成自然语言周报（不替代确定性 diff）。

---

## 附录 A：术语表

| 术语 | 说明 |
|------|------|
| Tracker Run | 一次追踪执行，关联一次 V1 Radar Run |
| Diff | 两次 Radar Run 之间某个 topic 的变化记录 |
| Alert | 达到阈值的 Diff，需要用户关注 |
| Contract | 一次 Tracker Run 的验收合同，确定性可回放 |
| Schedule | 工作区级的 cron 调度配置 |
| Magnitude | 变化幅度，0-100 归一化值 |

## 附录 B：与 V1 数据流对照

```
V1 数据流（不变）:
  workspace.run_workspace()
    → collector.run_once()
    → radar.run_discovery()
    → read_model.record_discovery()
    → API V1 读取

V2 数据流（新增，包裹 V1）:
  TrackerScheduler.tick()
    → TrackerScheduler.run_workspace_scan()    # 调用 V1
      → workspace.run_workspace()              # V1 不变
    → ChangeDetector.detect()                  # V2 新增
      → read_model.list_opportunities(prev)    # 读 V1
      → read_model.list_opportunities(curr)    # 读 V1
    → AlertDispatcher.dispatch()               # V2 新增
    → ContractVerifier.verify()                # V2 新增
```
