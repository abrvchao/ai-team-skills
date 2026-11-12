# Radar V2 P1/P2 Security Hardening — Independent Review

AI_TEAM_ARTIFACT_STATUS: completed

- **Review date**: 2026-11-12
- **Reviewer role**: independent verifier（独立复核人），read-only（只读）
- **Subject**: isolated Radar V2 hardening snapshot under `content-opportunity-radar/`
- **Baseline for delta**: the non-hardened copies in the main workspace (`content-opportunity-radar/`), compared with `diff -u`; the three tracker modules are new files there
- **Audit contract**: F1–F4 in `content-opportunity-radar/RADAR_V2_PRODUCTION_AUDIT_2026-11-11.md`
- **Verification style**: temporary SQLite fixtures（临时 SQLite 夹具）, fake scan callbacks, and offline HTTP tests only

## Verdict

**ACCEPT（通过）— scoped to the F1–F4 hardening changes.**

**This is NOT a production acceptance（非生产验收）.** No real Radar scan, no production database write, and no schedule enablement was performed or authorized. Product-level status remains **BLOCKED / not accepted for production** because F7（真实运行证据）is out of scope and still open.

Acceptance is **conditional**: the F1–F4 safety contracts pass under sequential/offline verification, but one P2 concurrency race means `run_now` is not *strictly* one-shot when the background scheduler is running. The race does not re-enable a disabled schedule or orphan rows, but it can duplicate provider work and produce a misleading manual-run status. It should be fixed before strict one-shot semantics are relied on in production.

Gate rationale:

- F1’s stated failure path (alerts persisted, then `complete_run` raises) is fixed and independently verified: rollback happens, alerts are deleted before diffs, the run ends `failed`, and no diff/alert rows remain.
- F2’s stated sequential paths are fixed and independently verified: a previously disabled schedule is disabled again after success, failure, and exceptions; an already-enabled schedule stays enabled.
- F3 is fixed and independently verified: `run_now` is scoped to one `schedule_id` and does not execute another workspace’s due schedule; the API path overrides any body-supplied `workspace_id`.
- F4 is fixed for the stated browser/CLI threat model: loopback peer, loopback Host, JSON Content-Type, same-origin `Origin` when present, and `Sec-Fetch-Site: cross-site` are all enforced.
- Residual P2/P3 items are listed below; none reintroduces the original permanent-enable bug, and none leaves orphan rows on the audited F1 path.

| Item | Result |
|---|---|
| F1 failure cleanup（失败清理） | **PASS** |
| F2 one-shot restore, sequential success/failure/exception（顺序路径） | **PASS** |
| F2 strict one-shot under concurrent background tick（严格单次） | **P2 residual race** |
| F3 selected-schedule isolation（目标调度隔离） | **PASS** |
| F4 local POST guard（本机写接口防护） | **PASS** (browser/CLI matrix) |
| Targeted tests | 33/33 OK on Python 3.13.1 and 3.10.15 |
| Full repository suite | **Not rerun** per the follow-up scope instruction |
| Production acceptance | **NOT GRANTED** (F7 missing; no real scan run) |

## Scope and Boundaries

In scope:

- `content-opportunity-radar/tracker.py`
- `content-opportunity-radar/tracker_runtime.py`
- `content-opportunity-radar/tracker_scheduler.py`
- `content-opportunity-radar/api.py`
- the accompanying new/adapted tests: `test_tracker_scheduler.py`, `test_tracker_runtime.py`, `test_tracker_api.py`, `test_tracker.py`, and the `test_radar_product.py` Content-Type adaptation

Out of scope: F5 (scope-change baseline reset), F6 (error-code granularity), F7 (real-run evidence), production deployment, and any paid/network provider calls.

Boundaries actually respected:

- No source, test, documentation, or existing file was modified. The only written file is this review artifact.
- No `.env`, credentials, private keys, runtime database, logs, or personal collaboration notes were read.
- No external service, paid API/model, real Radar scan, or schedule enablement was used.
- All probes used `tempfile.TemporaryDirectory()` and local SQLite. No `__pycache__` or `.pyc` was written (`PYTHONDONTWRITEBYTECODE=1`).
- The full-repository suite was not rerun, per the follow-up instruction; the targeted suite and independent probes are the evidence base here.

## Files and Functions Reviewed

### `content-opportunity-radar/tracker.py`

- `TrackerStore.due_schedules()` (lines 230–245): optional `schedule_id` filter used by F3.
- `TrackerStore.set_schedule_enabled()` (lines 247–257), `request_schedule_now()` (lines 280–294).
- `TrackerStore.start_run()` (lines 298–329), `set_run_previous()` (331–349), `complete_run()` (351–364), `fail_run()` (366–382).
- `TrackerStore.last_completed_radar_run()` (407–418): success watermark used by the race analysis.
- `TrackerStore.record_diffs()` (422–495), `list_diffs()` (497–521), `create_alerts()` (525–578), `list_alerts()` (580–600).
- `V2_SCHEMA` (34–109): the `tracker_alerts.diff_id → tracker_diffs.diff_id` foreign key that makes deletion order material.

### `content-opportunity-radar/tracker_scheduler.py`

- `parse_cron()` / `next_cron_due()` (50–99): UTC cron validation and next-due calculation.
- `ScanResult` (102–110): completeness/watermark contract.
- `TrackerScheduler._claim()` (124–137): conditional `UPDATE` claim with `enabled = 1`.
- `TrackerScheduler.tick()` (139–252):
  - `schedule_id` parameter and due-schedule filter (139–150);
  - scan/completeness/watermark validation (160–194);
  - diff persistence and alert closure (195–223);
  - failure cleanup: `rollback()` → delete alerts → delete diffs → `fail_run()` (230–251).

### `content-opportunity-radar/tracker_runtime.py`

- `RadarTrackerRuntime.initialize()` (48–71): disabled-by-default schedule creation.
- `create_schedule()` (112–137): explicit enable for the user-created schedule.
- `run_now()` (139–184): schedule selection, temporary enable, `request_schedule_now`, scoped `tick(schedule_id=...)`, `finally` restore.
- `tick()` and `_loop()` (186–220): global/background tick path used by the race analysis.
- `_scan_workspace()` (233–336): previous-run selection and complete-scan validation (context only).

### `content-opportunity-radar/api.py`

- `_request_json()` (79–94): `Content-Length` bound and JSON-object validation.
- `_local_action_allowed()` (96–137): loopback peer, loopback `Host`, `application/json`, `Sec-Fetch-Site`, optional same-origin `Origin`.
- `do_POST()` (545–609): guard placement before body read for `/v2/tick`, `/v2/workspaces/{id}/schedule`, `/v2/workspaces/{id}/run`, and the legacy `/v1/lite/*` action paths.
- `serve()` (625–663): background scheduler start/stop around the HTTP server.

### Tests reviewed

- `test_tracker_scheduler.py`: F1 alert-then-`complete_run` regression (179–208), failed-run cleanup (157–177), failure isolation (210–240), token-redaction regression (106–114), idempotent tick and claim (370–456).
- `test_tracker_runtime.py`: F2/F3 manual-run tests (56–180), free-source baseline test (182–229), disabled workspace (231–239).
- `test_tracker_api.py`: F4 reject/accept cases (120–139), handler dispatch (98–118).
- `test_tracker.py`: unchanged in the snapshot delta; not rerun (persistence is exercised indirectly by the targeted tests).
- `test_radar_product.py`: the invalid-JSON case now sends `Content-Type: application/json` so it reaches the JSON parser instead of being rejected by the guard (97–105).

### Change delta vs the non-hardened baseline

`diff -u` against the main-workspace copies shows a surgical delta limited to the hardening:

- `tracker.py`: `due_schedules` gained the optional `schedule_id` filter (F3 only).
- `tracker_scheduler.py`: `tick` gained the optional `schedule_id` filter; failure cleanup now calls `rollback()` and deletes `tracker_alerts` before `tracker_diffs`.
- `tracker_runtime.py`: `run_now` now creates a missing schedule as disabled, records `restore_disabled`, wraps request/tick in `try/finally`, and passes the selected `schedule_id` to `tick`.
- `api.py`: the simple peer-only guard was expanded to Host, Content-Type, Fetch Metadata, and Origin checks.
- Tests: F1/F2/F3/F4 regressions were added; the token fixture was renamed (see P3 finding).

No unrelated source edits were observed in the four scoped files.

## Commands and Tests Run

Environment: the default `/usr/bin/python3` is 3.9.6 and cannot import `core.py` (`dataclass(slots=True)` requires Python ≥ 3.10). The repository Dockerfile targets Python 3.12. Verification used `/opt/homebrew/bin/python3` 3.13.1 and `/opt/homebrew/bin/python3.10` 3.10.15. This is an environment caveat, not a code finding.

### Targeted repository tests

```text
cd content-opportunity-radar
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3 \
  -m unittest test_tracker_scheduler test_tracker_runtime test_tracker_api -v
# Ran 33 tests in 3.248s — OK

PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.10 \
  -m unittest test_tracker_scheduler test_tracker_runtime test_tracker_api
# Ran 33 tests in 3.262s — OK
```

The targeted suite covers the scheduler, runtime, and API test modules; the `TrackerStore` persistence layer is exercised through them. The wider offline suite was deliberately not rerun; no claim is made here about it.

### Independent probes

Each probe was an inline `python3 - <<'PY'` script run from `content-opportunity-radar/`, using `tempfile.TemporaryDirectory()` and a local SQLite file. F4 used a real `ThreadingHTTPServer` on `127.0.0.1:0` with a fake actions object, plus a unit-level fake handler for non-loopback peers. No repository test was modified.

## F1 — Alerts Already Persisted, Then `complete_run` Fails

`TrackerStore.complete_run` was replaced with four failing variants. In every variant the patched function first asserted that at least one alert row existed (the precondition), and the final state was read from a fresh connection where applicable.

| Scenario | Observed DB state |
|---|---|
| `complete_run` raises before any update | run `failed`; 0 diffs / 0 alerts for the run; 0 total |
| `complete_run` performs an uncommitted partial `UPDATE` then raises | partial state rolled back; `radar_run_id` is `NULL`; run `failed`; 0 / 0 |
| `complete_run` commits the run as `completed` then raises | run rewritten to `failed`; 0 / 0 |
| Control: delete diffs before alerts (reverse order) | `sqlite3.IntegrityError: FOREIGN KEY constraint failed` — order matters and is enforced |
| Two runs: `ws_ok` succeeds, `ws_fail` fails at `complete_run` | `ws_ok`: `completed`, 1 diff / 1 alert; `ws_fail`: `failed`, 0 / 0; totals 1 / 1 |

The delete-order capture observed exactly:

```text
DELETE FROM tracker_alerts WHERE tracker_run_id = ?
DELETE FROM tracker_diffs WHERE tracker_run_id = ?
```

Conclusion: F1 is correct for the audited path. The rollback undoes an uncommitted completion update, the child table is deleted before the parent, the run ends `failed` with `scan_execution_failed`, and no orphan diff/alert rows remain. Cross-run isolation also holds.

## F2 — One-Shot Restore

The schedule’s original `enabled` state was set before `run_now`; the scan callback produced either a complete result, an incomplete result, or an exception. A final read from a new connection recorded `enabled`.

| Initial schedule | Outcome | Observed final `enabled` |
|---|---|---|
| disabled | success (`completed`) | **0** |
| disabled | `ScanResult.complete=False` (`failed`) | **0** |
| disabled | exception escaping `TrackerScheduler.tick` (`run_now` raised) | **0** |
| disabled | exception in `request_schedule_now` (`run_now` raised) | **0** |
| enabled | success | **1** |
| enabled | incomplete scan | **1** |
| enabled | exception escaping `TrackerScheduler.tick` | **1** |
| enabled | exception in `request_schedule_now` | **1** |
| no schedule before `run_now` | success; a disabled schedule is created | **0** |
| two disabled schedules in one workspace | exactly one scan; both end disabled; the other stays due | **0 / 0** |

Conclusion: F2 is correct for all sequential success/failure/exception paths. The `finally` restores a previously disabled schedule and leaves an already-enabled schedule enabled. Unexpected exceptions propagate out of `run_now` (the API turns them into 500), but the enabled state is still restored in the tested cases.

**Caveat**: strict one-shot under a concurrent background tick is not achieved; see the P2 finding.

## F3 — Selected `schedule_id` Isolation

| Scenario | Observed |
|---|---|
| `ws A` and `ws B` both due; `run_now(A)` | scan calls `[A]`; B has 0 runs and remains enabled/due |
| one workspace, two enabled+due schedules; `run_now(A)` | exactly one scan call; both schedules remain enabled; the unselected one remains due |
| direct `TrackerScheduler.tick(schedule_id=sa)` with A and B due | scan calls `[A]` only |
| background `runtime.tick()` with no filter | both due schedules processed — expected global catch-up behavior, not the manual path |
| unknown workspace | `KeyError`; no runs created for the known workspace |
| API path A + body `workspace_id=B` for `/run` | action receives `workspace_id=A` (path wins) |
| API path A + body `workspace_id=B` for `/schedule` | action receives `workspace_id=A` (path wins) |
| URL-encoded workspace id `ws%2Fencoded` | decoded to `ws/encoded` and passed as a single id; no route escape |
| empty id, extra path segment, unknown suffix | 404; no action dispatched |
| `/v2/tick` with body `workspace_id` / `schedule_id` | body reaches the action, but `RadarProductActions.tracker_tick` ignores it and calls the global runtime tick — this endpoint is a global catch-up endpoint by design, not the manual `/run` path |

Conclusion: F3 is correct. The API takes the workspace ID from the path, overwrites any body-supplied value, and `run_now` selects only that workspace’s schedule and passes its `schedule_id` to the SQL-filtered `tick`. The global `/v2/tick` endpoint remains able to process all due schedules by design; it is local-only and separate from the manual-run contract.

## F4 — POST Guard Matrix

Live server bound to `127.0.0.1:0`; HTTP requests used `http.client` (loopback peer). Custom `Host` cases used `skip_host=True` so the test client did not add a duplicate Host header; an initial run produced three false “failures” for that reason, and those cases were re-run correctly.

| Request | Result |
|---|---|
| no `Origin`, JSON, loopback Host (CLI-style) | 200 |
| same-origin `Origin` (`http://127.0.0.1:PORT`) | 200 |
| same host, different port `Origin` | 403 |
| `Origin: http://localhost:PORT` while Host is `127.0.0.1:PORT` | 403 |
| `Origin: null` | 403 |
| `file://` Origin | 403 |
| `Origin: http://evil.example` | 403 |
| `Origin: https://127.0.0.1:PORT` (same netloc) | 200 — strictness note |
| `Origin` uppercase scheme/host | 200 |
| `Origin` with a path suffix (`http://127.0.0.1:PORT/path`) | 200 — malformed but ignored; strictness note |
| `Origin: http://evil@127.0.0.1:PORT` | 403 (hostname loopback but netloc mismatch) |
| `Origin: http://127.0.0.1:[REDACTED_EMAIL]` | 403 |
| `Content-Type: application/json; charset=utf-8` | 200 |
| `Content-Type: Application/JSON` | 200 |
| `Content-Type: text/plain` with a valid JSON body | 403 |
| `Content-Type: application/x-www-form-urlencoded` | 403 |
| `Content-Type: multipart/form-data` | 403 |
| no `Content-Type` | 403 |
| `Content-Type: application/json, text/plain` | 403 |
| `Sec-Fetch-Site: cross-site` | 403 |
| `Sec-Fetch-Site: CROSS-SITE` | 403 |
| `Sec-Fetch-Site: same-site` with no `Origin` | 200 — defense-in-depth note |
| `Sec-Fetch-Site: same-origin` | 200 |
| `Host: evil.example` | 403 |
| `Host: localhost:PORT` | 200 |
| `Host: [::1]:PORT` | 200 |
| `Host: localhost.:PORT` (trailing dot) | 403 |
| `Host` mismatched with `Origin` | 403 |
| `Host: localhost:PORT` + matching `Origin` | 200 |
| missing `Host` | 403 |
| `OPTIONS` preflight | 501, no CORS headers |
| legacy `/v1/lite/scan` without Content-Type | 403 |
| legacy `/v1/lite/scan` same-origin JSON | 200 |
| valid JSON guard pass but invalid JSON body | 400 from `_request_json` |

Unit-level guard checks:

| Case | Result |
|---|---|
| peer `127.0.0.1` | allowed |
| peer `127.0.0.2` | allowed (127/8 is loopback) |
| peer `::1` | allowed |
| peer `203.0.113.9` | denied |
| peer `10.0.0.5` | denied |
| non-loopback peer + forged `X-Forwarded-For: 127.0.0.1` | denied |
| non-loopback peer + loopback `Host` | denied |
| loopback peer + missing `Host` | denied |
| loopback peer + missing `Content-Type` | denied |

Conclusion: F4 is correct for the stated threat model. Browser simple requests are blocked by the JSON Content-Type requirement; a cross-origin JSON `fetch` hits the 501 preflight or the `Origin`/`Sec-Fetch-Site` check; CLI callers without `Origin` work when they send `Content-Type: application/json`. No browser-exploitable bypass was found.

## Concurrency Probe — F2 Strict One-Shot

Two forced interleavings put a background `TrackerScheduler` at the enable/claim boundary. The probe patched `TrackerStore.request_schedule_now` to run a second `TrackerScheduler` on a separate connection, either before calling the original request (interleaving A) or after it (interleaving B). This demonstrates reachable sequences, not a measured production probability.

| Interleaving | Observed |
|---|---|
| A: disabled past-due schedule; background claims after the temporary enable, then `request_schedule_now` resets `next_due_at = now` | **2 scan callbacks**, **2 tracker runs** (background `completed` with `radar-1`; manual `failed` with `previous_snapshot_missing`), final `enabled = 0` |
| B: already-enabled due schedule; background claims after `request_schedule_now`, before the manual tick | background run `completed`; **1 scan callback**; `run_now` returned `failed` with empty `items`; final `enabled = 0` |

The schedule still ends in the correct enabled/disabled state, so the original permanent-enable bug is fixed. The residual issue is duplicate provider work, extra run rows, and a misleading manual-run status under a narrow race.

## Findings by Severity

### P2 / Medium — `run_now` is not strictly one-shot against the background scheduler

**Where**: `tracker_runtime.py:167–177` (temporary enable, `request_schedule_now`, scoped `tick`, `finally`), `tracker_runtime.py:211–220` (background loop), `tracker_scheduler.py:139–150`, `tracker.py:280–294`.

**What**: A previously disabled schedule is made due by the temporary enable (if its `next_due_at` is already past) and/or by `request_schedule_now`. The background loop (started by `api.serve` when the action object exposes `start_background_scheduler`) can claim the schedule in either window. If it claims before `request_schedule_now`, the manual request resets `next_due_at` and the manual tick can claim it again, producing two scan callbacks and two tracker runs for one `run_now`; the manual run then fails because the background run changed the success watermark. If the background claims after `request_schedule_now`, `run_now` may return `failed` with empty `items` even though the background run completed.

**Impact**: duplicate scans (free-provider quota, extra run rows) and a misleading `failed` status. It does **not** leave the schedule enabled, does not orphan diff/alert rows, and does not reintroduce the original “manual verification silently becomes recurring collection” hazard.

**Condition / recommendation**: Before relying on strict one-shot semantics in production, make the manual path a single claim that does not need a separate enable step, or serialize manual runs against the background loop per schedule (for example, a dedicated one-shot claim/lease, a process-wide lock keyed by `schedule_id`, or a “manual hold” that the background loop honors). At minimum, document the race and add a regression test for the forced interleaving above.

**Classification rationale**: P2 rather than P1 because the primary safety property (schedule restored to disabled; no orphan rows) holds in the observed interleavings. It is still a real contract gap: the audit wording says manual run must be “one-shot（单次执行）”.

### P3 / Low — F1 cleanup is not atomic and is not `BaseException`-safe

**Where**: `tracker_scheduler.py:230–251`.

**What**: The handler does `rollback()` → `DELETE tracker_alerts` → `DELETE tracker_diffs` → `fail_run()`, relying on `fail_run()`’s `commit()` to commit the deletes. This is an implicit transaction. If a delete or `fail_run()` fails (for example, SQLite lock/disk error), the deletes can be rolled back on connection close, leaving a `running` run and orphan diffs/alerts. Also, `except Exception` does not catch `KeyboardInterrupt`/`SystemExit`, so an interrupt during diff/alert persistence can leave a `running` run.

**Evidence**: patching both `complete_run` and `fail_run` to raise produced, after reconnect, `status=running`, 1 diff, 1 alert. Patching `complete_run` to raise `KeyboardInterrupt` produced the same `running` + 1 diff + 1 alert state. The audited path (`complete_run` raises, `fail_run` works) is fixed and passes.

**Recommendation**: Wrap delete-alerts + delete-diffs + mark-failed in one explicit transaction; if crash consistency is required, use a `BaseException`-safe cleanup path or a startup repair that marks stale `running` runs failed and removes their children.

### P3 / Low — F2 restore failure can leave a schedule enabled

**Where**: `tracker_runtime.py:174–177`.

**What**: The `finally` calls `set_schedule_enabled(schedule_id, False)` without a retry or repair path. If that call raises (for example, a transient DB lock), the schedule stays enabled, which is the original permanent-enable hazard. A SIGKILL/power loss between the temporary enable and the `finally` has the same effect; there is no persistent one-shot lease.

**Evidence**: patching the `enabled=False` call to raise produced `run_now` raising `restore_failed` and a final `enabled = 1`.

**Recommendation**: retry the restore, persist a “temporary manual enable” marker with a startup reconciliation that disables it, or use a one-shot claim/lease that does not depend on a `finally` to undo a persistent flag.

### P3 / Low — Token-redaction regression test is vacuous

**Where**: `content-opportunity-radar/test_tracker_scheduler.py:106–114` (`test_exception_does_not_leak_token`).

**What**: The exception fixture was renamed to a concatenated Bearer literal (`"authorization: Bearer " + "fixture-token-not-real"`), but the assertion still checks for the old `private_value` sentinel. That assertion would pass even if the runtime persisted the raw exception text. The runtime currently stores only the fixed `scan_execution_failed` code, so this is a test-quality regression, not a live leak.

**Recommendation**: Assert against the renamed fixture (or one shared non-secret sentinel) in both the return value and `str(runs)`, so the test actually proves redaction.

### P3 / Low — F4 strictness and coverage observations (no browser bypass found)

**Where**: `api.py:96–137`; `test_tracker_api.py:120–139`.

**Observations**:

- The `Origin` scheme is only checked to be `http`/`https`; it is not bound to the scheme the request arrived on (the handler is plain HTTP). An `https://` Origin whose `netloc` equals `Host` is accepted on the HTTP listener. I could not construct a browser-exploitable path because the same host:port cannot serve both the HTTP API and attacker-controlled TLS content; this is a strictness note, not a bypass.
- `Origin` with a path suffix is accepted because only `netloc` is compared. Browsers send an origin without a path; this is malformed-input tolerance, not a browser path.
- `Sec-Fetch-Site: same-site` with no `Origin` is accepted. Modern browsers send `Origin` on cross-origin POST; this is defense-in-depth only. Consider rejecting any value other than `same-origin`/`none` when the header is present.
- CLI callers without `Origin` work only when they send `Content-Type: application/json`; a bare POST without that header now gets 403. This is intended by the F4 contract, but should be documented for CLI users.
- The repository tests do not cover a non-loopback peer, a loopback Origin with a different port, `Origin: null`, form/multipart bodies, or a missing Host. The independent matrix did; the implementation rejected all of them fail-closed.
- The guard does not verify that the `Host` port is the actual server port. A loopback reverse proxy that forwards a remote request with a loopback Host and no Origin would satisfy the guard; keep the service bound to `127.0.0.1` and do not expose it through such a proxy.

### P3 / Low — The “concurrent claim” test is sequential

**Where**: `test_tracker_scheduler.py:423–456` (`test_two_schedulers_same_schedule_only_one_wins`).

**What**: The test creates two connections but calls `sched_a.tick(now=T0)` and then `sched_b.tick(now=T0)` sequentially in one thread. It proves idempotent re-claim, not simultaneous contention. The conditional `UPDATE` in `_claim()` is the correct mechanism and the SQL semantics are sound, but the test name overstates what is exercised.

**Recommendation**: Use a barrier and two threads (or directly assert the conditional UPDATE’s `rowcount` under two connections) and add the forced `run_now`/background interleaving as a regression test.

### P3 / Low — New manual-run test is wall-clock dependent

**Where**: `test_tracker_runtime.py:51–54` and `174–180`.

**What**: `T0` is fixed at `2026-10-10`, while `initialize()` uses the real clock and the due assertion uses `datetime.now(timezone.utc)`. The tests pass on 2026-11-12 but would fail on a host clock before 2026-10-10 (or near it, if the next cron due equals `T0`). Prefer an injected fixed `now` or a `T0` derived from the current time.

## Test Quality Assessment

Strengths:

- The new F1 regression test checks the alert-present precondition inside the patched `complete_run`, so it genuinely covers “alerts persisted, then completion fails.”
- The F2 tests cover disabled-after-success, disabled-after-failure, enabled-remains-enabled-after-success, and target-schedule isolation.
- The F4 tests use a real HTTP server and real headers, not a mock of the guard.
- The targeted suite is offline and fast (33 tests in ~3.2s).

Gaps (covered by independent probes, not by repository tests):

- F1: no test for an uncommitted partial `complete_run` update or a commit-then-raise; the independent probe covered both. No test for cleanup failure (`fail_run` raising) or `BaseException`.
- F2: no test for an exception escaping `TrackerScheduler.tick`, an already-enabled schedule after failure, the no-schedule branch, multiple schedules per workspace, or a restore failure; the independent probe covered the first five.
- F3: no test for two due schedules in the *same* workspace; the independent probe covered it.
- F4: no test for a non-loopback peer, a loopback Origin on a different port, `Origin: null`, form/multipart bodies, or missing Host; the independent matrix covered them.
- Concurrency: the claim test is sequential; the `run_now`/background race has no regression test.
- The token-redaction test is vacuous (P3 finding above).
- The manual-run test’s due assertion is wall-clock dependent.

## Implementation vs Stated Safety Contract

| Contract item | Assessment |
|---|---|
| F1: alert rows persisted, `complete_run` fails → rollback partial state, alerts before diffs, run failed, no diff/alert rows | **PASS** — verified for three failure variants plus FK-order control and cross-run isolation. P3 atomicity gap if cleanup itself fails or a `BaseException` escapes. |
| F2: previously disabled schedule disabled again after success, failure, exception; already-enabled remains enabled | **PASS (sequential)** — verified for success, incomplete scan, exception in `tick`, exception in `request_schedule_now`, already-enabled success/failure/exception, no-schedule, and multiple schedules. **P2 caveat** under concurrent background tick. |
| F3: manual run restricted to selected `schedule_id`; cannot execute another workspace’s due schedule | **PASS** — API overrides body `workspace_id` from the path; `run_now` selects only that workspace’s schedule; `tick(schedule_id=...)` filters the SQL claim. `/v2/tick` remains a global catch-up endpoint by design. |
| F4: loopback peer + loopback Host + `application/json` + same-origin `Origin` when present + `Sec-Fetch-Site` cross-site rejection; simple requests and CLI without `Origin` considered | **PASS** — live HTTP matrix plus unit-level peer/Host checks; no browser simple-request path found. P3 strictness notes only. |

## Residual Risks

1. **F5（scope-change baseline）** — out of scope; no assessment.
2. **F6（error-code granularity）** — still present: `complete_run`/database failures are folded into `scan_execution_failed` at `tracker_scheduler.py:232–236`, limiting diagnosis. The stored message remains redacted, which is good.
3. **F7（real-run evidence）** — no real Radar scan, no production run/diff/alert evidence. Production acceptance is therefore **not** granted.
4. **Crash safety（崩溃安全）** — `run_now` restores the schedule in a `finally`, but a SIGKILL/power loss between the temporary enable and the `finally` leaves the schedule enabled. There is no persistent one-shot lease. This is outside the audited “success/failure/exception” wording but is a real operational risk.
5. **Cleanup atomicity（清理原子性）** — a database failure during the F1 cleanup can leave a `running` run or orphan rows; see the P3 finding.
6. **Global `/v2/tick`** — the local-only endpoint processes all due schedules and is intentionally not scoped to one workspace. It is not the manual `/run` path, but any local caller can use it; keep the loopback trust boundary intact.
7. **Reverse proxy（反向代理）** — if a loopback reverse proxy terminates a remote request and forwards it with a loopback Host and no `Origin`, the peer/Host checks are satisfied. Keep the service bound to `127.0.0.1`; do not expose it through a proxy.
8. **Legacy browsers（旧浏览器）** — the guard relies on `Origin`/`Sec-Fetch-Site` when present and on the JSON Content-Type requirement. Very old browsers that do not send `Origin` are outside the tested matrix; `application/json` is non-safelisted, so modern browsers preflight or block.
9. **CLI compatibility（命令行兼容性）** — no-`Origin` CLI calls require `Content-Type: application/json`. A bare POST is rejected with 403 by design; document this.
10. **Origin scheme（Origin 协议）** — `https://` same-netloc Origin is accepted on the HTTP listener; no exploit found, but the check is not a full same-origin scheme comparison.
11. **Concurrency（并发）** — the P2 `run_now`/background race; the schedule state ends correctly, but duplicate scans and status noise are possible.
12. **Time-dependent tests（时间依赖测试）** — the new F3 test and the `next_due_at > T0` assertion depend on the wall clock relative to 2026-10-10.

## What Was Not Done

- No production database, runtime logs, `.env`, credentials, private keys, or personal notes were read.
- No real Radar scan, provider call, paid API/model, or schedule enablement was performed.
- No source, test, or documentation file was modified.
- The full repository test suite was not rerun, per the follow-up instruction; adjacent collector/workspace tests were outside the requested scope and were not used as evidence.
- No claim of production acceptance is made.

## Reproduction Notes

- All repository tests were run from `content-opportunity-radar/` with `PYTHONDONTWRITEBYTECODE=1`.
- All independent probes used `tempfile.TemporaryDirectory()` and local SQLite; F4 used a live `ThreadingHTTPServer` bound to `127.0.0.1:0` with a fake actions object.
- The concurrency finding is demonstrated by a forced interleaving (the probe inserts a background tick at the enable/claim boundary). It is a reachable sequence, not a measurement of natural production timing.
- The F4 custom-Host cases initially showed false failures because `http.client` added a duplicate Host header; after switching to `skip_host=True`, the guard rejected `Host: evil.example`, trailing-dot localhost, and missing Host, and accepted loopback `localhost`/`127.0.0.1`/`[::1]` with matching origins.
- No `__pycache__` or `.pyc` was written; the only persistent artifact is this file.

## Sign-off

Independent verifier: **ACCEPT（通过）for the F1–F4 hardening scope**, with the P2 concurrency race and P3 test/cleanup observations recorded as follow-ups.

Production acceptance: **NOT GRANTED**. F7（真实运行证据）remains open; no real production scan was authorized or run. Do not present this review as production acceptance.
