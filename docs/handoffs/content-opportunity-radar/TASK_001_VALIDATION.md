# TASK-001 Validation

Issue: `abrvchao/ai-team-skills#26`
Branch: `validation/task-001-radar-mainline`
Base: `ba225fa` (`main`, merge of PR #25)

## Environment

- macOS 26.6.2 arm64
- Python 3.13
- OpenSSL 3.4.0
- Docker Desktop 4.36.0 / Engine 27.3.1
- Docker Compose v2.30.3-desktop.1

## Full test suite

Command:

```bash
/opt/homebrew/bin/python3.13 -m unittest discover -v
```

Result: **101 tests passed** after the TASK-001 fixes.

Findings fixed during validation:

- GDELT received the default scope `AI`, which its DOC API rejects as a two-character keyword. The provider now removes short ASCII terms (`AI agents` becomes `agents`) and falls back to `news` if no usable term remains. HTTP 403/429 responses are reported as `rate_limited` with the status instead of being reported as an opaque JSON decode failure.
- Workspace-scoped Radar runs shared append-only artifact paths (`snapshots.jsonl`, `provider-cache.json`, `page-snapshots.jsonl`) across workspaces, which would cross-contaminate time-series history. `run_discovery` now namespaces those artifacts under a per-workspace directory.
- The Dashboard could render a stale workspace's response when switching workspaces quickly; a load token and per-request context guard were added (client-side, manually reviewed; not covered by the Python suite).

## Workspace onboarding

Using a temporary SQLite database, the following passed:

- created a workspace with a primary domain and competitor;
- duplicate competitor input was deduplicated;
- generated six scoped jobs (GitHub, Hacker News, Google News, GDELT, own site, competitor site);
- generated plan contained no secret-shaped values;
- ran the workspace once against live providers;
- persisted a workspace-scoped read-model run and top opportunity;
- GDELT degraded independently while the other providers continued successfully.

The workspace isolation and dashboard async-load protections described above were added or retained and covered by the complete test suite (Python side); the Dashboard guard is client-side JavaScript and was verified only by smoke-rendering.

## Docker Compose smoke test

Project: `task001-smoke`; volume: `task001-smoke_radar-data`.

Passed:

- built the `python:3.12-slim` image;
- started `radar-web` and `radar-worker` from an empty volume;
- web container became healthy;
- `GET http://127.0.0.1:8787/v1/health` returned `{"status":"ok"...}`;
- Dashboard HTML rendered with title `SignalDesk — Content Opportunity Radar`;
- worker prepared `/data/radar.db` and `/data/collection-plan.json`;
- worker collected GitHub, Hacker News, and Google News while isolating GDELT degradation;
- stopped and recreated both containers without deleting the named volume;
- health endpoint remained available and both persistent files remained in the shared volume.

The temporary Compose project was removed after verification; the named test volume was also removed after the evidence run.

## GDELT TLS investigation

TLS itself is healthy: an HTTPS request reaches `api.gdeltproject.org` and returns HTTP 200. The original response body was HTML, not JSON:

```text
Your search contained a keyword that was too short.
```

The default `AI` scope was the trigger. After query normalization, the current environment returns HTTP 429 (`Too Many Requests`) rather than a JSON decode error; the provider now reports that explicitly as `rate_limited` and remains non-blocking for the rest of Radar.
