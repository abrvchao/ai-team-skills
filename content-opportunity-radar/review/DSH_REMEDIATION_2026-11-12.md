# DSH Independent Review — Remediation Record (2026-11-12)

## Source review

The independent DSH report is preserved in DSH_RADAR_V2_P1_P2_REVIEW_2026-11-12.md. Its scoped verdict was ACCEPT for sequential/offline F1–F4 checks, but it identified a P2 race between manual run_now and the background scheduler, plus several P3 test and cleanup gaps. The source report predates the remediation below and must not be read as approval of the final revision.

## Findings addressed

1. **P2 — manual/background race and duplicate scan risk**
   - Added TrackerStore.claim_and_start_run: the conditional schedule claim and insertion of the running tracker run now commit in one SQLite transaction.
   - request_schedule_now no longer toggles enabled; it refuses to overwrite a schedule while its workspace already has a running run.
   - Manual run_now can include a disabled schedule for exactly one targeted tick, while leaving the schedule disabled throughout. If another tick claimed first, it returns the existing run state rather than re-queueing a duplicate.
   - Added a runtime interleaving test with a background scan held open while run_now is called; one provider scan and one tracker run are asserted.
   - Added a true two-thread, barrier-synchronized same-schedule claim test.

2. **P3 — failure cleanup atomicity and BaseException**
   - Alert deletion, diff deletion, and failed-run status update now execute in one transaction, with child rows deleted before parent rows.
   - fail_run supports caller-managed commit.
   - Scheduler cleanup handles BaseException; it re-raises KeyboardInterrupt/SystemExit after cleanup rather than swallowing them.
   - Added a KeyboardInterrupt regression test after alert persistence.

3. **P3 — token-redaction test**
   - The assertion now checks the exact synthetic fixture token and the stored run result, rather than an unrelated placeholder.

4. **P3 — API boundary and test coverage**
   - Host must be loopback and its port must match the listening port.
   - Browser Origin must be HTTP, same-origin, and contain no path/query/fragment/userinfo.
   - Sec-Fetch-Site accepts only same-origin or none; both same-site and cross-site are rejected.
   - Added negative cases for malformed Origin, wrong scheme/port, form content types, Fetch Metadata, spoofed Host, and non-loopback peer.
   - Documented CLI compatibility: Origin may be omitted only for JSON requests to the local service; the service must not be exposed through a remote reverse proxy.

5. **P3 — time-dependent test**
   - Replaced the fixed historical timestamp with timestamps derived from the test clock.

6. **P2 runtime latency — GDELT**
   - The GDELT request timeout was reduced from 25 seconds to 5 seconds because the provider was unreachable in this environment and its sequential per-candidate timeout could stall a full scan.
   - Added tests for the bounded timeout and graceful degraded result.

## Verification after remediation

- Radar Python full suite: **393/393 PASS**.
- Radar focused regression set: **67/67 PASS**.
- Node Worker tests: **214/214 PASS**.
- Orchestrator Python tests: **169/169 PASS**; two MCP test modules remain unexecuted because the local interpreter lacks the optional mcp SDK.
- Isolated live smoke: two real runs using free public sources, temporary SQLite, 3 candidates and deep_limit 2; both completed, producing 2 tracker runs and 3 diffs, 0 alerts (no alert threshold crossed), the second run linked to the first baseline, and the schedule remained disabled.
- Dashboard JavaScript syntax checks and git diff --check passed.

## Remaining acceptance boundary

- A follow-up DSH review of this remediation revision is still required.
- F5/F6 diagnostics and baseline-management follow-ups remain P3.
- Production acceptance remains BLOCKED: no production database was written, no production scan was run, and no recurring schedule was enabled. A production one-shot scan requires explicit user approval.
