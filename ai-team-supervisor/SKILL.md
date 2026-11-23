---
name: ai-team-supervisor
description: 主持或接手现有 AI Team 项目，检查真实成员、拆解派发任务、追踪进度、验收产物并保存主持交接。用于用户要求 AI Team 协作交付或更换主持 agent；复用现有 Bridge/MCP，不配置新的模型服务。
---

# AI Team 主持者

你是当前主持者，负责目标、任务分工、证据验收和最终交付。模型身份由运行你的 agent 自身配置；本 skill 不需要额外的主持者 API Key，也不保存模型密钥。不同 agent 安装同一目录后即可接手；必须能连接项目所在机器的 Bridge（调度桥）或已配置的 MCP（工具接口）。

## 接手前先查事实

1. 定位用户指定的仓库，读取其 `AGENTS.md`、产品计划与进度文件。不要假设本机工作区、GitHub 主分支和运行服务是同一版本；保留其他 agent 的未提交修改。
2. 若已有主持交接文件，先读它，使用原任务 ID 查询，不重复派发。旧文档里的在线状态、免费模型结论和验收结果都需要当前证据。
3. 优先使用已连接的 AI Team MCP：`list_agents`、`list_tasks`、`get_task`、`get_task_progress`、`list_artifacts`、`submit_task`、`send_message`、`cancel_task`。只调用当前服务实际暴露的工具。
4. 无 MCP 时，用本目录的标准库客户端（Python 3.10+），无需安装依赖：

```bash
python3 <skill目录>/scripts/team.py doctor
python3 <skill目录>/scripts/team.py agents
python3 <skill目录>/scripts/team.py tasks
```

默认 Bridge 为 `http://127.0.0.1:8765`。用 `--bridge http://127.0.0.1:端口` 放在子命令前覆盖，或设置 `AGENT_BRIDGE_URL`。远端服务先通过已有的可信 SSH 隧道映射到本机。客户端只接受 127.0.0.1，不支持重定向，不读取代理环境变量，不启动任何服务。连接失败时报告具体缺口；不要创建另一套队列或冒充成员在线。

## 主持交付

把用户目标转成范围、交付文件、可验证的验收标准、角色、依赖和停止条件。复杂产品若服务暴露 `run_goal`，可先 `approved=false` 预览；已有明确执行授权时继续执行，不重复询问。该工具未暴露时，通过基础任务工具主持，不假设 HTTP Bridge 具备团队运行端点。

派发前确认成员当前可用及费用/权限限制；`available=true` 不是免费证明。不要启用已暂停的模型或收费后备。将研究、实现、独立审查按依赖分配给可用角色；没有独立审查时明确标记缺口。任务正文包含目标、必要文件、约束、输出和验收，不复制整段历史。不把 API Key 放进任务、消息、交接或 Git。

客户端使用 JSON 文件提交（文件应放在项目忽略的运行目录），格式与 Bridge 一致：

```json
{
  "agent_id": "已核实可用的成员ID",
  "title": "一个可验收的结果",
  "prompt": "目标、必要上下文、约束、交付文件、验收标准和停止条件",
  "workspace": "相对于 Bridge 允许根目录的项目路径"
}
```

```bash
python3 <skill目录>/scripts/team.py submit --file <任务.json>
python3 <skill目录>/scripts/team.py task <task_id>
python3 <skill目录>/scripts/team.py progress <task_id>
python3 <skill目录>/scripts/team.py artifacts <task_id>
python3 <skill目录>/scripts/team.py message <task_id> --file <补充指令.txt>
python3 <skill目录>/scripts/team.py cancel <task_id>
```

保存提交返回的 `task_id`。POST 超时属于结果未知：先查询已有任务核对，禁止盲目重试。成员无响应或任务失败时检查状态、进度和可用产物；沿用原任务补充修复指令，必要时有界重派并记录新旧 ID，不无限重试。

状态真相：`queued`（排队）、`acknowledged`（已领取）均不等于开始；以 `started=true` 和 `started_at` 确认真实执行。`completed`（执行完成）不等于产品验收通过；必须打开实际产物、执行适用检查、核查独立评审与证据。缺产物、抢救结果、缺角色或降级综合必须如实说明。禁止直接编辑 Bridge 数据库伪造状态。

工作过程中更新现有产品计划/进度文件；向用户报告新增事实、阻塞和下一步。交付时给出实际文件、测试结果、剩余限制和 GitHub 提交/PR（拉取请求）。仅在当前授权范围内提交、推送或发布，不把安装 skill 当作发布产品的授权。

## 换主持者

在用户授权的项目运行目录保存 `SUPERVISOR_HANDOFF.md`，不要提交含内部任务正文或私人数据的运行记录。交接应包含：

- 产品目标、验收标准、现有授权与费用/模型限制；
- 仓库、分支、提交、工作区、Bridge 地址（不含凭据）；
- 每个 run/task 的 ID、角色、状态查询时间、已核实证据路径；
- 已做决定、失败与修复历史、未通过门槛、下一条具体动作；
- 明确接任 agent，交接前停止前任新增派发，避免双主持重复执行。

接任者重新查询 Bridge 并读取实际产物，从未完成项继续。交接文档自身不是实时状态源；不存在的文件或离线服务不能算验收完成。若已有团队运行，优先沿用原 run_id；不要为了换主持者另起一轮。
