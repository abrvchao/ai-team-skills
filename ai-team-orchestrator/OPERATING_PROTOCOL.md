# AI Team Operating Protocol V1

This protocol keeps multi-agent work efficient, truthful, and cheap enough to
run continuously.

## Roles

### Supervisor
Owns:
- architecture and task decomposition;
- agent selection;
- interfaces and acceptance criteria;
- cross-agent review;
- merge/reject decisions;
- final user-facing status.

The Supervisor should not do repetitive repository scans, bulk field mapping,
routine test execution, log cleanup, or documentation harvesting when a worker
can do it.

### Worker
Owns delegated execution:
- DSH: implementation, debugging, tests, repository work;
- Gemini: research, long-context synthesis, multimodal review once connected;
- Grok: trend/social research and adversarial review once connected.

A worker may not silently change architecture or acceptance criteria.

## Token-efficiency rules

1. **Delta context only.** Send the worker only what changed plus stable
   references to repository files/issues/PRs. Do not resend full project history.
2. **Read narrow.** Start from explicit files/functions. Expand only when a
   dependency requires it.
3. **Batch reads.** Prefer one targeted batch over many tiny repeated reads.
4. **Artifact over chat.** Store long logs, research tables, generated code and
   test output as files/commits/artifacts. Return only paths + concise findings.
5. **No full logs by default.** Return failing command, error class, relevant
   excerpt, and artifact path.
6. **Stable task prefix.** Keep invariant instructions stable between retries so
   prompt caching/context reuse is possible.
7. **No repeated summaries.** Status updates contain only new facts.
8. **One decision owner.** Workers provide evidence/options; Supervisor decides.
9. **Stop duplicate work.** Before starting, check task id, branch, PR, artifact
   and current status.
10. **Truth before convenience.** Token savings never justify skipping a test,
    fabricating an ACK, or claiming work started when it did not.

## Delegation truth states

```
queued        = accepted by control plane, worker has not started
acknowledged  = worker leased/accepted task, still not running
running       = real worker task handler entered execution
blocked       = worker started but needs an external dependency
completed     = acceptance output produced successfully
failed        = worker execution failed
cancelled     = Supervisor/user stopped task
```

Only `running` with a real `started_at` may be described as “Agent started”.

## Core work vs dirty work

Supervisor keeps:
- system architecture;
- protocol/schema changes;
- security boundaries;
- scoring/business logic;
- merge and release gates;
- final synthesis.

Delegate by default:
- directory/repository scanning;
- API field inventories;
- repetitive provider spikes;
- batch source verification;
- fixture generation;
- unit/integration test execution;
- log triage;
- docs cleanup;
- compatibility matrices;
- data sampling;
- benchmark runs.

## Standard task packet

Every delegated task should fit this shape:

```json
{
  "task_id": "stable-id",
  "agent_id": "dsh",
  "objective": "one concrete outcome",
  "context": {
    "repo": "owner/repo",
    "base_ref": "main",
    "files": ["only/files/needed"],
    "issue": 0,
    "prior_artifacts": []
  },
  "constraints": [
    "architecture/security/truth rules"
  ],
  "outputs": [
    "artifact/commit/PR/test result"
  ],
  "acceptance": [
    "binary/verifiable checks"
  ],
  "do_not_do": [
    "unrelated redesign",
    "repeat completed work",
    "paste full logs"
  ],
  "output_budget": {
    "summary_lines": 12,
    "log_excerpt_lines": 40
  }
}
```

## Worker completion response

Workers should return only:

```
STATUS: completed|failed|blocked
TASK: <task_id>
CHANGES: <3-7 concise bullets>
TESTS: <command + result>
ARTIFACTS: <paths/commit/PR>
RISKS: <new risks only>
BLOCKER: <one blocker or none>
```

Do not reproduce files or logs that already exist as artifacts.

## Retry protocol

When review fails:

1. Supervisor sends only the failed acceptance checks and relevant diff/file refs.
2. Worker modifies the existing branch/task; do not start a duplicate task.
3. Worker returns only changed facts and new test results.
4. Supervisor re-runs/inspects only affected gates plus required regression suite.

## Multi-agent review

For complex work:

```
Worker A → implementation/research artifact
Worker B → independent review of artifact
Supervisor → reconcile disagreements + final decision
```

Worker B receives the artifact and acceptance criteria, not Worker A's full
conversation.

## Status shown to user

Every project checkpoint ends with:

```
当前阶段：...
下一步：...
你要做：... / 你不用做任何事
我来做：...
```

The user should never have to infer the next action from a development log.
