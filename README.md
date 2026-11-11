# AI Team — AI-native Product Studio

AI Team is an experimental product-development system for one question:

> Can a non-developer turn a vague idea into a working product with a team of heterogeneous AIs?

The project is **not** trying to become another general-purpose personal agent, chat gateway, memory framework, or skill marketplace. Those are increasingly commodity capabilities and should be borrowed from OpenClaw, Hermes, or equivalent runtimes when useful.

AI Team owns the layer above them:

```text
Idea / problem
    ↓
Product Supervisor
    ↓
role-specialized heterogeneous models
    ↓
research + implementation + real-environment execution
    ↓
mandatory cross-review
    ↓
evidence / artifact / acceptance gates
    ↓
resilient recovery and reassignment
    ↓
working product + measured iteration
```

## What must be distinctive

1. **Heterogeneous model specialization** — different model families perform roles they are actually suited for.
2. **Resilient Product Supervisor** — timeouts, quota failures, missing artifacts, or unavailable workers degrade gracefully through convergence, salvage, reassignment, and explicit coverage gaps.
3. **Mandatory independent review** — important work is challenged by another worker against concrete acceptance criteria before synthesis.
4. **Real-world workers** — the team works against real repositories, runtimes, browsers, devices, APIs, files, and eventually Android/China-app environments.

## What we deliberately do not compete on

Prefer integration/reuse for generic agent loops and chat UX, personal memory, generic skill discovery, messaging gateways, cron, browser/terminal wrappers, and provider plumbing when mature runtimes already expose them.

See:
- [Product positioning and build-vs-borrow boundary](docs/AI_TEAM_POSITIONING.md)
- [V1 milestone plan](docs/AI_TEAM_V1_PLAN.md)
- [Roadmap](docs/ROADMAP.md)
- [Ordered backlog](docs/BACKLOG.md)
- [Progress reporting protocol](docs/PROGRESS_PROTOCOL.md)
- [Current program status](docs/AI_TEAM_STATUS.md)
- [V0.2 architecture and acceptance gates](docs/AI_TEAM_V0_2_ARCHITECTURE.md)
- [Operating protocol](ai-team-orchestrator/OPERATING_PROTOCOL.md)

## Current proving ground

`content-opportunity-radar/` is the first dogfood product. It tests whether this AI product team can research, build, validate, review, and iterate a real product from a non-developer's original idea.

## Current specialist workers

- **DSH** — engineering verification, repository work, runnable tests.
- **Gemini** — deep research and long-context synthesis.
- **Grok** — external reality checks and adversarial review.
- **Qwen** — implementation architecture and structured decomposition.
- **Doubao** — Chinese-language and China-ecosystem context.

Workers are replaceable. The value is the orchestration contract and diversity of independent evidence, not allegiance to a vendor.
