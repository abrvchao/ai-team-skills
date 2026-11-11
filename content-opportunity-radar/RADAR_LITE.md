# Radar Lite（精简版）

## Lite v0.1 status（精简版 v0.1 状态）

Progress（进度）: **5/5 = 100% — operational（可用）**

- [x] Discover（发现）: bounded native live scan（有界原生实时扫描）
- [x] Rank + Evidence（排序 + 证据）: Top cards with citations / provider coverage（Top 机会卡 + 引用 / 来源覆盖）
- [x] Handoff（交接）: deterministic HANDOFF.json（确定性交接包）
- [x] AI Team Intake（AI Team 接入）: HANDOFF.json → Product Contract draft（产品合同草案），批准前不启动 Worker
- [x] End-to-end Delivery（端到端交付）: mandatory product artifact（强制产品成品）落盘 + independent validation（独立验收）

Latest live proof（最新实时证据）:

- Radar run: `5adae128ccae0e6dd51bf3a2`
- Top-3: agent skills / coding agent / agentic workflow
- Handoff ready（可交接）: **3/3**
- Fresh rank #1 → AI Team contract draft: `contract_c719a182f9845dc2`
- Worker run started（是否启动 Worker）: **no**
- First live delivery run（首个真实交付运行）: `run_ebec6a6f2ad0a23bbef3` → **FAILED truthfully（真实失败）**; discovery / handoff / primary / synthesis worked, but Implementation did not create a mandatory product deliverable and Validation therefore had no valid artifact to accept.
- Core fix（核心修复）: HANDOFF now declares a deterministic `artifact_path`; Product Contract carries it as a mandatory deliverable; Supervisor refuses to enter Validation when that deliverable is missing/incomplete.
- Successful live delivery rerun（真实交付复跑成功）: contract `contract_afc6f50f666c9e16` → run `run_8cd5453912dcbf3e3af4` → **completed / independent verdict=accept**.
- Mandatory product artifact（强制产品成品）: `.radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md`.
- Execution truth（执行真相）: single-worker degraded start（单 Worker 降级启动）because only Qwen was healthy; Grok still performed independent Review / Validation（独立审查 / 验收）. Historical Worker failures remain preserved; final accepted validation resolved active errors.
- Schedule（日程）: **completed**, within the original project due window.
- Full Radar regression（全量回归）: **176/176 PASS（通过）**
- AI Team full regression（AI Team 全量回归）: **121/121 PASS（通过）**

## 当前产品边界

Radar Lite 只保留最短价值链：

1. Discover（发现）— 使用当前稳定的 native（原生）发现链；
2. Rank（排序）— 输出 Top 机会；
3. Evidence（证据）— 保留 Research Pack（研究包）和 citation（引用）；
4. Handoff（交接）— 生成标准 HANDOFF.json，交给 AI Team。

用户不需要知道机会具体怎么执行；AI Team 负责把机会变成可审阅交付物。

## 一个入口

查看现有机会，不触发外部采集：

    python3 radar_lite.py cards --scope "AI Agents" --top 5

运行一次精简发现：

    python3 radar_lite.py scan --scope "AI Agents" --top 5 --output .radar/lite/LATEST.md

把第 1 名机会生成 AI Team 交接包：

    python3 radar_lite.py handoff --scope "AI Agents" --rank 1 --output .radar/lite/HANDOFF.json --markdown-output .radar/lite/HANDOFF.md

HANDOFF.json 可以继续通过 AI Team 的 draft_radar_handoff_contract（Radar 交接合同起草工具）生成 Product Contract（产品合同）；批准后才会启动 Worker。

## Deferred / Restartable（延后 / 可重启）

以下能力不是删除，只是不进入 Radar Lite 当前默认路径：

| 能力 | 当前状态 | 重新启动条件 |
| --- | --- | --- |
| GSC first-party（GSC 第一方） | DEFERRED / externally blocked | 有真实 owned/authorized property |
| Native vs AIHOT | DEFERRED | Lite 在真实使用中证明需要更多种子覆盖 |
| China-social providers（中国社交源） | DEFERRED | 明确出现中国内容机会覆盖缺口 |
| More providers（更多数据源） | DEFERRED | 现有源导致重复可证明的发现盲区 |
| Google Ads / paid demand（付费需求数据） | DEFERRED | 真实需求验证需要商业搜索量证据 |
| Complex Dashboard（复杂面板） | DEFERRED | Lite CLI/现有面板无法支持真实使用 |
| New scoring dimensions（新评分维度） | DEFERRED | 真实错误排序能归因到缺失维度 |

原则：**先用 Lite，缺什么再重新启动什么；不是因为“能做”就外扩。**
