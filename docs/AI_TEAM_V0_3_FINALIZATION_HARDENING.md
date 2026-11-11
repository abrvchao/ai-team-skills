# AI Team V0.3 Finalization Hardening（AI Team V0.3 收尾机制加固）

Date（日期）: 2026-10-03

## Trigger（触发原因）

Graduation Dogfood 004（毕业实战 004）`run_55fee0bfb4e43e056982` failed before review because all three currently available workers failed during the primary phase（主角色阶段）:

- DSH: timeout（超时）;
- Grok: quota_exhausted（额度耗尽）;
- Qwen: timeout（超时）.

The batch fallback planner（批量回退规划器） correctly refused to reassign work to workers already unhealthy in the same run, so the run failed truthfully rather than creating a fallback loop（回退死循环）.

## Root cause（根因）

The execution-budget contract（执行预算合同） and Supervisor deadline（主管截止时间） contradicted each other:

- task metadata declared a long exploration budget（探索预算）;
- Supervisor soft deadline（主管软截止） forced convergence much earlier;
- finalization still allowed model tool exploration（模型工具探索）, so protected delivery time was not actually protected.

## Fix（修复）

1. Supervisor budget generation（主管预算生成） now derives worker exploration/finalization time from the same soft/hard deadlines（软/硬截止时间）.
2. Qwen finalization（Qwen 收尾） is delivery-only（只交付）:
   - exploration（探索） continues through Qwen CLI（Qwen 命令行工具）;
   - finalization（收尾） switches to a separate OpenAI-compatible text API（兼容 OpenAI 的纯文本接口） request that sends no `tools` or `tool_choice` fields（不发送工具定义或工具选择字段）;
   - output is plain text（纯文本）;
   - the Adapter（适配器） persists the final text into the mandatory artifact（必交成果文件）.
3. Qwen CLI invocation（Qwen 命令行调用） uses explicit `--prompt` to avoid ambiguous array/positional argument parsing（数组参数/位置参数解析歧义）.
4. DSH keeps its native artifact-writing path but receives the aligned, longer protected finalization budget（受保护收尾预算）.

## Real adapter evidence（真实适配器证据）

### Qwen

Real product-like smoke task（真实产品级冒烟任务） `task_a1f9d10ac0b0dfb9d58c`:

- exploration used `read_file`（读文件） before convergence（收敛前允许探索工具）;
- after `forced_finalization`（强制收尾）, tool calls = **0**;
- finalization used the independent text-only API channel（独立纯文本接口通道） rather than Qwen CLI（Qwen 命令行工具）;
- task status = `completed`（已完成）;
- mandatory artifact（必交成果） changed from checkpoint（检查点） to `AI_TEAM_ARTIFACT_STATUS: completed`（成果文件已完成）;
- report explicitly preserved `NOT VERIFIED / EVIDENCE GAP`（未验证 / 证据缺口）.

### DSH

Real smoke task（真实冒烟任务） `task_b7db056088ad81cf8797`:

- Supervisor sent forced finalization（主管发送强制收尾）;
- DSH rewrote the checkpoint into a substantive report within the protected finalization budget（受保护收尾预算）;
- task status = `completed`（已完成）;
- report explicitly recorded evidence gaps（证据缺口） instead of fabricating verification.

## Regression evidence（回归证据）

- Qwen Adapter（Qwen 执行适配器）: 12/12 PASS（通过）
- DSH Adapter（DSH 执行适配器）: 13/13 PASS（通过）
- Supervisor focused（主管调度器聚焦测试）: 37/37 PASS（通过）
- Orchestrator non-MCP（编排器非 MCP 回归）: 67/67 PASS（通过）
- Python compile check（Python 编译检查）: PASS（通过）
- Git diff check（Git 差异格式检查）: PASS（通过）

## Conclusion（结论）

M1.3 Exploration / Finalization Budget（探索 / 收尾双预算） is again considered complete only after these real-adapter checks（真实适配器验证）, not merely unit tests（单元测试）.
