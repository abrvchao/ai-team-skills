# AI Team Product Positioning

Status: architecture boundary for V0.2 and later.

## Product thesis

AI Team is an **AI Project Delivery System（AI 项目交付系统）** with an AI-native Product Studio（AI 原生产品工作室） inside it. The user states a goal; the system turns that goal into a Delivery Plan（交付计划）, assigns heterogeneous Workers（执行器）, schedules bounded work, executes, monitors/replans around failures, independently validates the result, and delivers a usable artifact.

The target user is not required to be a professional developer or project manager. The system should minimize the user's operational burden: the user makes major product decisions, while AI Team owns decomposition, worker planning, schedule control, execution convergence, evidence, acceptance, and delivery truth.

The canonical loop is:

```text
Goal（目标）
  → Product Contract（产品合同）
  → Delivery Plan（交付计划：工作分解 / Worker / 依赖 / 日程 / 截止）
  → Execute（执行）
  → Progress + Schedule Control（进度 + 日程控制）
  → Reassign / Repair / Replan（改派 / 修复 / 重排）
  → Independent Validation（独立验收）
  → Delivery（交付）
```

## Non-goal: another general-purpose agent platform

AI Team should not compete with mature agent runtimes on breadth. OpenClaw, Hermes, and similar projects already invest heavily in generic runtime capabilities such as sessions, memory, skill loading, chat channels, tool plumbing, provider normalization, delegation, and browser/terminal access.

Those capabilities are infrastructure. AI Team may integrate them, run on top of them, or replace one with another. They are not the moat.

## Build vs borrow

| Capability | Decision | Why |
| --- | --- | --- |
| Generic agent loop | BORROW | Commodity runtime capability |
| Chat/TUI/messaging gateways | BORROW | Not related to product-development quality |
| Personal long-term memory | BORROW | Mature implementations already exist |
| Generic skills marketplace/loader | BORROW | Use compatible standards/directories |
| Generic browser wrapper | BORROW | Treat browser as worker/tool transport |
| Generic terminal wrapper | BORROW | Treat terminal as execution substrate |
| Generic cron/scheduler | BORROW | Integrate only when a workflow needs it |
| Model-provider normalization | BORROW where practical | Vendor plumbing should not dominate roadmap |
| Heterogeneous role assignment | BUILD | Central product hypothesis |
| Product task contract | BUILD | Goal, artifact, evidence, stop condition, acceptance |
| Delivery planning / schedule control | BUILD | Turn the goal into auditable work, worker assignments, dependencies, due times and replanning truth |
| Cross-model independent review | BUILD | Required to reduce correlated model error |
| Evidence/artifact truth gates | BUILD | Product work must be auditable |
| Resilient Supervisor | BUILD | Recovery and degraded continuation are core value |
| Worker fallback/reassignment | BUILD | Required for real multi-provider operation |
| Product-run provenance | BUILD | Must say who did what, with what evidence |
| Real-world worker adapters | BUILD thin adapters | Local repo, WebCodex, device, China ecosystem differentiate us |
| Product iteration loop | BUILD | Connect research → build → validation → next decision |

## The four owned capabilities

### 1. Heterogeneous model specialization

Workers are selected for role fit, not symmetry. A useful run should contain meaningfully different reasoning/evidence paths. DSH, Gemini, Grok, Qwen, and Doubao are today's roster, not permanent product dependencies.

### 2. Resilient Product Supervisor

The Supervisor owns execution convergence rather than merely dispatch:

```text
assign
  → schedule
  → observe
  → force bounded convergence
  → validate artifact
  → salvage usable work if execution terminates badly
  → reassign failed role when useful
  → continue in explicitly degraded mode
  → synthesize with coverage gaps
```

A five-worker request must never be presented as five-worker consensus when only two or three workers actually contributed.

### 3. Independent review and gates

For material decisions:

```text
primary artifact
  → independent reviewer
  → failed acceptance checks / counter-evidence
  → bounded repair
  → synthesis
```

Review is based on the artifact and acceptance criteria, not on the primary worker's persuasive narrative.

### 4. Real-world workers

AI Team should test conclusions against the actual environment: repositories and tests, local processes and files, browser sessions and public web evidence, APIs and databases, Android devices / ADB experiments, and Chinese consumer platforms.

The core contract should be transport-agnostic: workers expose capability, truthful status, progress, artifacts, and failure classes.

## Stop-building list

Until the four owned capabilities pass V0.2 acceptance, do **not** prioritize:

- another general chat UI;
- a new memory system;
- a skill marketplace;
- Telegram/Discord/Slack gateways;
- more model vendors merely to increase worker count;
- self-hosted equivalents of mature browser/terminal runtimes;
- persona systems;
- billing/multitenancy/SaaS administration;
- generic workflow builders;
- Kubernetes/microservices for scale we do not yet have;
- features whose only justification is “OpenClaw/Hermes has it”.

Exceptions require a concrete product-run blocker that cannot reasonably be solved by integration.

## Product test

> A non-developer can state a goal and receive a visible plan, worker allocation, schedule, progress/blocker updates, and a working independently-validated result, while only making genuinely important product decisions.

Content Opportunity Radar is a dogfood（内部实战） test case, not the product definition.

## Decision rule for future architecture changes

Before adding a core capability, answer:

1. Does this directly improve idea → working-product convergence?
2. Does it strengthen heterogeneous collaboration, review, evidence truth, resilience, or real-environment execution?
3. Is this already a mature commodity capability in OpenClaw, Hermes, or another runtime?
4. Could we integrate it behind a thin adapter instead?

If (1) and (2) are both no, or if (3) and (4) are both yes, keep it out of AI Team core.
