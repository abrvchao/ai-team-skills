# AI Team V0.3 Dogfood 004（AI Team V0.3 第 4 次毕业实战）

Date（日期）: 2026-10-03  
Run（运行）: `run_1e64490d0d2f8a6dda2a`  
Product（产品）: `content-opportunity-radar`  
Final run state（最终运行状态）: **blocked（阻塞）**

## Goal（目标）

修复中文候选发现中过度裁剪合法复合词的问题，例如：

- `智能支持系统`
- `开源包含套件`
- `多模态支持平台`

同时保持：

- `NVIDIA 推出`
- `AI 发布`
- `13 个基准`

等低质量片段继续被抑制，并保持既有英文行为。

## What the run proved（本次运行证明了什么）

- DSH、Grok、Qwen 三个真实 Worker（执行器）进入 Primary（主角色）执行；
- Grok 真实触发 `quota_exhausted（额度耗尽）`；
- `run_local_health（运行内健康状态）` 将 Grok 标记为 unhealthy（不健康），禁止继续接新任务；
- Batch Role Planner（批量角色规划器）持久化完整 fallback plan（替补规划）后，将 Red Team（红队）角色交给健康的 Qwen；
- Soft Deadline / Force Convergence（软截止 / 强制收敛）在 primary、review、synthesis、implementation、validation 多阶段真实触发；
- 两条 structured review（结构化独立审查）通过 review-v0.2 gate（审查结构门槛）；
- Synthesis（综合决策）完成；
- DSH 在真实产品 workspace（工作区）执行 Implementation（实现）；
- Qwen 独立执行 Validation（验证）。

M1.6 Graduation Dogfood（毕业实战）因此已经满足 2/4 条：

- [x] 至少三个独立产品角色贡献有效成果；
- [x] 至少一个 Worker 失败并被健康感知重新分配；
- [ ] 至少一次自动 `repair → revalidation（修复 → 重新验证）`；
- [ ] 产品在无人工逐任务编排下达到最终 acceptance（验收通过）。

## Why the run blocked（为什么被阻塞）

Validator（验证者）把 Git HEAD（当前提交）当成“本次 run 开始前的产品状态”，并执行 English parity（英文一致性）比较。

但 Radar 工作区在 run 启动前已经是 dirty worktree（存在未提交改动），其中 `agentic` scope-gating（agentic 范围门控）已经是产品现状并有测试覆盖。`PRODUCT_PROGRESS.md` 在本次 run 前已经记录该能力通过 126/126 tests（126/126 测试）。

因此：

```text
Git HEAD
  ≠
Run-start product state（运行开始时产品状态）
```

旧 parity harness（英文一致性脚本）把 pre-existing dirty feature（运行前已存在的未提交功能）误判成本次 Implementation（实现）带来的 regression（回归）。

验证结果因此给出：

`VERDICT: blocked（结论：阻塞）`

这不是中文算法失败，而是 provenance baseline（变更来源基线）错误。

## Product truth after investigation（调查后的产品真实状态）

恢复 run 前已经存在的 `agentic` scope-gating 后：

- Chinese/English focused tests（中英聚焦测试）: **25/25 PASS**
- Full Radar regression（Radar 全量回归）: **129/129 PASS**

所以产品当前实现本身是绿的；Dogfood 004 不能算毕业通过，是因为 run-level validation provenance（运行级验证来源）不可信。

## Supervisor fix（Supervisor 修复）

新增 `workspace-baseline-v0.1（工作区基线 v0.1）`。

每个新 run 在写入 `.ai-team-runs` 之前保存：

- Git HEAD commit（提交基线）；
- run-start tracked diff（运行开始时已跟踪文件差异）；
- run-start status（运行开始时状态）；
- untracked manifest（未跟踪文件清单）；
- untracked file size + SHA-256（未跟踪文件大小 + 哈希）。

未跟踪文件内容不会被自动复制进 artifact（成果），避免无意打包敏感内容。

Implementation / Validation（实现 / 验证）现在明确遵守：

> Run-start truth（运行开始时真相） = HEAD + saved baseline（提交基线 + 保存的运行前差异），不能只看 HEAD。

Validation（验证）只有比较 run-start baseline 与 run-end state（运行结束状态）后，才能把差异归因于本次 run。

回归验证：

- Supervisor tests（主管测试）: **38/38 PASS**
- Orchestrator non-MCP regression（编排器非 MCP 回归）: **68/68 PASS**
- `py_compile（Python 编译检查）`: PASS
- `git diff --check（Git 差异格式检查）`: PASS

## Grok credential follow-up（Grok 凭据补强）

本次 run 中 Grok 使用当前登录/免费额度路径并触发 quota exhaustion（额度耗尽）。运行环境检查显示：

- Grok CLI（命令行客户端）存在；
- `XAI_API_KEY` 未注入 Worker 环境。

新增 `XAI_API_KEY_FILE（xAI 接口密钥文件）` 支持，让本地 Account Management / Secret Store（账号管理 / 密钥存储）可通过只读文件向 Worker 注入凭据，而无需在聊天、argv（命令参数）或日志中暴露密钥。

Grok Worker regression（Grok 执行器回归）: **12/12 PASS**。

## Next gate（下一关）

Dogfood 004 不算 M1 graduation（M1 毕业）。下一场必须：

1. 使用 Baseline Snapshot（基线快照）作为验证真相源；
2. 选择代码中尚未修复的新真实缺陷；
3. 继续要求真实 Worker failure（执行器失败）可恢复；
4. 必须真实触发至少一次 Automatic Repair Loop（自动修复循环）；
5. Repair 后由不同 Worker 独立 Revalidation（重新验证）；
6. 最终 acceptance（验收）通过后才关闭 M1 Gate（M1 关卡）。

## Grok credential operational closure（Grok 凭据运维闭环）

Dogfood 004 结束后，桌面账号管理中的 xAI API Key（xAI 接口密钥）已通过安全本机路径接入：

- Secret File（密钥文件）仅本机用户可读写；
- AI Team 使用独立 `GROK_HOME（Grok 配置目录）`，不复用个人登录态；
- `ai.team.grok` 已改为正式 macOS LaunchAgent（用户级后台服务）；
- Bridge（桥接服务）真实派发的 Grok smoke task（冒烟任务）完成并产出合法 artifact（成果文件）。

因此后续 Grok 任务不再依赖本机免费 Grok Build 登录额度。密钥明文不进入代码、任务 metadata（元数据）、argv（命令行参数）或日志。

## Post-run repair fallback hardening（运行后修复阶段替补加固）

Dogfood 004 后续恢复流程真实触发了 Automatic Repair Loop（自动修复循环），但第一轮 DSH Repair Worker（DSH 修复执行器）在没有完成 `repair_report.md（修复报告）` 的情况下失败。旧逻辑会直接让整个 run 失败，即使其他健康 Worker 仍可用。

Supervisor 已补齐 Repair-stage fallback（修复阶段替补）：

- 失败进程仍保留为 failed（失败），不伪装成功；
- 失败修复会消耗一次 bounded repair budget（有界修复预算）；
- 如果仍有预算且存在健康 Worker，则自动换修复者；
- 下一位修复者不能重复使用已失败的 repair worker（修复执行器）；
- 仍然受 `max_repair_attempts（最大修复次数）` 限制。

回归证据：

- Supervisor tests（主管测试）: **42/42 PASS**
- Orchestrator non-MCP regression（编排器非 MCP 回归）: **72/72 PASS**
- `py_compile（Python 编译检查）`: PASS
- `git diff --check（Git 差异格式检查）`: PASS

因此 Dogfood 004 最终计入 M1.6 Graduation Dogfood（毕业实战）**2/4**：角色贡献与 Worker failure recovery（执行器失败恢复）已验证；完整 `repair → revalidation（修复 → 重新验证）` 和最终 acceptance（验收）仍需 Dogfood 005 证明。
