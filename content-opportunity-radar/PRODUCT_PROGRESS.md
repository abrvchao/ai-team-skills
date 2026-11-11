# Content Opportunity Radar — Product Progress

Last updated: 2026-10-13

## Third-party source change（第三方数据源变更）— 2026-10-13

- Jisu（极速数据）API subscription expired（接口已到期）; disabled and rejected even when an old approved configuration exists.
- Juhe（聚合数据）is the sole current candidate for third-party API integration; its live endpoint, quota, and authorization must still be verified before any external request.

## Third-party API preparation（第三方 API 接入准备）— 2026-10-13

- Added `third_party_api.py` as offline, disabled-by-default Juhe（聚合数据）/ Jisu（极速数据）configuration and spending gate; no API key loaded and no network call executed.
- Safety（安全约束）: HTTPS host allowlist, no URL query credentials, env-var secret references, per-run call/cost limits; unknown prices and unapproved paid calls fail closed.
- Offline tests: 4/4 PASS（通过）; full Radar regression: **323/323 PASS（通过）**.
- Status（状态）: **configuration gate only（仅配置门禁）**; real endpoint adapters and authorized live API checks remain pending.
- Next（下一步）: select concrete eligible endpoints, write mock-response adapters, then perform separately authorized live checks.


## Snapshot

- Overall status: **operational_lite_first（精简版可用 / 真实使用）**
- Current product mode（当前产品模式）: **Radar Lite local product v0.1（Radar 精简版本地产品）**.
- Current gate: **Operational Use（真实使用）**
- Product implementation/regression baseline: **190/190 tests PASS（通过）**; Dashboard app.js + i18n.js JavaScript syntax PASS.
- Current blocker: **none for Lite core（精简核心无阻塞）**.
- Next action: use the product on real scopes; restart one Deferred / Restartable（延后 / 可重启） module only when real usage exposes that specific need.

## Stage progress

| Stage | Checklist | Progress | State |
| --- | ---: | ---: | --- |
| R0 Signal foundation | 3/3 | 100% | complete |
| R1 Opportunity discovery | 4/4 | 100% | complete |
| R2 Product-facing workflow | 4/4 | 100% | complete |
| R3 Real-user validation | 0/4 | 0% | current |
| R4 Productization decision | 0/3 | 0% | not_started |

Checklist roll-up: **11/18 checks complete (61%)**. This is delivery progress, not an estimate of commercial readiness.

## R3 active work

- [ ] Human precision@5（人工前五精度） gate: **25/25 真人判断已全部收齐**。冻结的 binary gate（二元门槛）当前可计 **24 labels / 22 unique topics**，micro precision@5 = **0.90**；label-count / unique-topic / precision 三项子门槛均已通过。唯一未通过的是 all_top_k_complete=false：AI Engineering（AI 工程）#4 被真人明确判为 **weak relevance（弱相关）**，因此不应被强迫改写成 true/false。graded relevance（分级相关性）目前有 **11 个明确分级样本**，总体均值 **0.7879**；AI Tools（AI 工具）本组均值 **0.6667**。
- [ ] Exercise GSC flow with real authorized first-party data（真实授权第一方 GSC 数据）.
- [ ] Put the current workflow in front of a real target user/design partner and record whether the opportunities are actionable（真实用户可行动性验证）.
- [ ] Compare experimental AIHOT/China-social seeds against native discovery using the same human-label protocol（实验种子与原生发现使用同一人工协议比较）.

### Investment stop-loss — 2026-10-06（投资止损）

- Formal AI Team decision run（正式 AI Team 决策运行）: `run_94a73384c898cd14ef4b` → **PIVOT（转向）— conditional（有条件）**.
- Radar is now under **feature freeze（功能冻结）**. New providers / models / product surfaces / scoring changes / broad multi-worker research are not authorized.
- Native vs AIHOT provider promotion（原生 vs AIHOT 数据源晋级）remains paused, but the **AIHOT standalone comparison deployment（AIHOT 独立对比部署） has been explicitly restarted by user request on 2026-10-08**. It is isolated from Radar Lite and does not auto-feed Radar scoring.
- Action Card human revalidation（行动卡真人复验）has already happened; its old “用户本人不会执行 → KILL” interpretation was withdrawn because that protocol measured downstream execution knowledge, not Radar value.
- Previous stop-loss rule（旧止损规则）“用户本人不知道怎么执行 → KILL”已因 protocol error（协议错误）撤销。新的止损必须基于 corrected Radar value / handoff validation（纠正后的 Radar 价值 / 交接验证）。
- Canonical policy（正式策略）: `RADAR_INVESTMENT_GATE.md`.

### Action Card repair revalidation — 2026-10-06（行动卡修复后真人复验）

- Human response（真人反馈）: **“12345我都不知道怎么做”**.
- Current real Top-5 after Action Cards（行动卡后的真实 Top-5）: **0/5 knows the first concrete step（0/5 知道第一步怎么做）**.
- Result meaning（结果含义）: 用户本人缺少执行知识，不能据此否定 Radar 的发现 / 排序价值。
- Previous KILL/FREEZE interpretation（此前停止结论）: **invalidated by protocol correction（因协议定义错误撤销）**.
- Correct next test（正确下一步）: **Radar → AI Team Handoff Test（Radar → AI Team 交接测试）**.
- No feature expansion（仍不扩功能）.
- Evidence（证据）: .radar/r3/design-partner/ACTIONABILITY_REVALIDATION.md and actionability-revalidation.json.

### Radar → AI Team handoff probe — 2026-10-06（Radar → AI Team 交接探针）

- Bounded run（有界运行）: run_2ac5bc1246812bf3ed89，topic = Agent Skills（智能体技能）.
- DSH successfully converted existing Radar evidence into a review-ready Chinese content package（DSH 已把 Radar 既有证据转换成可评审中文内容包）, including title options, audience, thesis, outline, Skill / Tool / MCP boundary explanation, checklist, risks, and evidence gaps.
- This demonstrates that the Radar opportunity itself can be handed downstream without requiring the user to know how to execute it manually.
- The probe also exposed an AI Team Review robustness issue: the staged primary file existed, but one Reviewer failed to read it and returned insufficient_evidence.
- AI Team fix（AI Team 修复）: Review prompts now embed a bounded authoritative target excerpt in addition to staging the full file, so review no longer depends entirely on Worker file-tool success.
- Regression after fix（修复后回归）: TeamSupervisor 44/44 PASS；AI Team full regression 111/111 PASS.
- The probe was intentionally stopped before implementation to respect the new cost discipline; its terminal failed status reflects the deliberate synthesis cancellation, not a Radar product verdict.

### Radar → AI Team full handoff — 2026-10-06（Radar → AI Team 正式端到端交接）

- Canonical run（正式运行）: `run_7c0916934a88dcf8418c` → **completed / 100%**.
- Source opportunity（源机会）: Agent Skills, Radar run `36f2958873957e3d830de79f`, Rank #1.
- Deterministic Handoff Package（确定性交接包）: `.radar/r3/handoff/agent-skills/HANDOFF.json` + `HANDOFF.md`.
- Research Pack evidence（研究包证据）: **19/19 resolved, 100% citation coverage（100% 引用覆盖）**.
- Primary（主任务）: Grok + Qwen both completed with real Radar citations and explicit evidence gaps.
- Independent cross-review（独立交叉审查）: **2/2 substantive accept（2/2 实质审查通过）** after Review finalization-context hardening.
- Final deliverable（最终成果）: `.radar/r3/handoff/agent-skills/CONTENT_BRIEF.md`.
- Independent Validation（独立验收）: **VERDICT: accept**; all five acceptance checks passed.
- Machine traceability（机器可追溯性）: source run / Rank #1 / scores / confidence preserved; **15 cited IDs used, 0 invalid IDs**; `supply_gap / authority_fit / business_fit` all preserved.
- Radar full regression（Radar 全量回归）: **172/172 PASS（通过）**.
- Product conclusion（产品结论）: the Radar → AI Team handoff value chain is **validated once end-to-end（已完成一次端到端验证）**.
- R3 accounting（R3 记账）: remains **0/4**; this handoff success does not impersonate the frozen human / GSC / seed-comparison gates.
- Canonical evidence（正式证据）: `.radar/r3/handoff/agent-skills/HANDOFF_RESULT.md`.

### Handoff repeatability sample #2 — Multi Agent（交接可重复性样本 #2 — 多智能体）

- Source opportunity（源机会）: Multi Agent, same Radar run `36f2958873957e3d830de79f`, Rank #2.
- Deterministic Handoff Package（确定性交接包）: **20 citations / 100% citation coverage**.
- Evidence transport（证据传递）: **PASS** — source run / scope / rank / scores / confidence / evidence gaps all preserved.
- Final producer artifact（主执行器最终成果）: **15 valid citation IDs / 0 invalid IDs**, with all three evidence gaps preserved.
- Delivery-format acceptance（交付格式验收）: **FAIL** — the Worker returned a correct evidence analysis but omitted the required audience / angle / title / outline / draft opening / Go-No-Go sections.
- One bounded format-only Repair（一次有界格式修复）was attempted and still missed the full section contract.
- Stop-loss（止损）: **triggered for this sample** — no third model retry.
- Root interface gap（接口根因）: Handoff Package had evidence rules but no formal Deliverable Contract（交付物合同）.
- Fix（修复）: Handoff Package now carries `content-brief-v1` with explicit required sections + acceptance conditions; Agent Skills and Multi Agent packages regenerated.
- Repeatability conclusion（可重复性结论）: **evidence handoff repeated successfully 2/2; downstream deliverable-format repeatability remains unproven**.
- Canonical evidence（正式证据）: `.radar/r3/handoff/multi-agent/HANDOFF_RESULT.md`.
- No additional dedicated repeatability run is authorized now; validate the new contract naturally on the next real delivery.

### Standard AI Team intake — 2026-10-06（标准 AI Team 接入）

- Added deterministic Radar Handoff Intake（确定性 Radar 交接接入器）: ai-team-orchestrator/radar_handoff_intake.py.
- HANDOFF.json now maps directly into a Product Contract（产品合同） with context_artifacts, acceptance, evidence requirements, stop-loss resource mode, and salvage|degrade fallback.
- Added MCP tool（MCP 工具）: draft_radar_handoff_contract — creates the contract draft only; Worker execution still requires approve_product_contract.
- Real Agent Skills HANDOFF.json static intake check: PASS; generated contract contract_914db29ae9207eb7 with run_id=None, so no Worker/model execution was spent.
- Multi Agent HANDOFF.json static intake check: PASS; generated contract contract_a1a002542df029bf with run_id=None.
- Standard intake repeatability（标准接入可重复性）: **2/2 PASS**, both preserving Radar source + handoff context without starting Workers.
- AI Team focused MCP/intake regression: 14/14 PASS（通过）.
- AI Team full regression: 116/116 PASS（通过）.

## Latest evidence

### Radar Lite — 2026-10-07（Radar 精简版）

- Lite v0.1 core progress（精简核心进度）: **5/5 = 100% — operational（可用）**.
- Added one product entrypoint（单一产品入口）: `radar_lite.py`.
- Lite chain（精简链路）: Discover（发现） → Rank（排序） → Evidence（证据） → Handoff（交接）.
- `cards` uses the existing read model and does **not** trigger external collection; real AI Agents Top-5 smoke test: **PASS**.
- Real Top-5 all report `handoff_ready=true`; rank #1 Agent Skills handoff carries **19 traceable citations（19 条可追溯引用）**.
- `handoff` reuses the existing deterministic `radar-ai-team-handoff-v1` contract instead of inventing a second format.
- `scan` is intentionally bounded to native seeds, Research Pack enabled, and GSC / Google Ads / page hydration disabled by default.
- Full Radar regression after Lite core: **176/176 PASS（通过）**.
- Expansion registry（外扩清单）: `RADAR_EXTENSIONS.json`; paused capabilities are **Deferred / Restartable（延后 / 可重启）**, not removed.
- Fresh live native scan（新鲜原生实时扫描）: run `5adae128ccae0e6dd51bf3a2` → Top-3 = agent skills / coding agent / agentic workflow; **3/3 handoff_ready（可交接）**.
- Fresh rank #1 handoff → AI Team Product Contract draft（产品合同草案）: `contract_c719a182f9845dc2`; `run_id=None`, therefore no Worker/model execution was started by the validation.
- First approved live delivery（首个批准后的真实交付）: `run_ebec6a6f2ad0a23bbef3` failed truthfully. Radar discovery / Handoff / Primary / degraded Synthesis completed, but Implementation produced only a blocked implementation report and no final `CONTENT_BRIEF.md`; Validation then failed without a `validation_report.md`. This run remains historical evidence and is not rewritten.
- Core repair（核心修复）: Handoff `deliverable_contract` now carries deterministic `artifact_path`; AI Team Product Contract propagates it as `deliverable_artifacts`; Implementation must create both its report and the real product artifact; Supervisor independently blocks Validation when the product artifact is absent/in-progress; Validation prompt explicitly inspects mandatory deliverables.
- Successful approved live rerun（批准后真实复跑成功）: contract `contract_afc6f50f666c9e16` → run `run_8cd5453912dcbf3e3af4` → **completed / independent verdict=accept / active errors=0**.
- Real deliverable（真实交付物）: `.radar/lite/deliveries/agent-skills/CONTENT_BRIEF.md`, preserving Radar run / scope / rank / scores / confidence / citation IDs / evidence gaps.
- Run truth（运行真相）: only Qwen was healthy at start, so the run truthfully degraded to one primary Worker; Grok supplied independent Review and Validation. Historical task failures remain visible; final accepted validation moved obsolete repair errors into `resolved_errors`.
- Verdict compatibility hardening（验收结论兼容加固）: explicit `VERDICT: PASS` now normalizes to internal `accept`; prose remains rejected.
- Final regression（最终回归）: TeamSupervisor **50/50 PASS**; AI Team full **121/121 PASS**; Radar Lite focused **6/6 PASS**; Radar full **176/176 PASS**.

### Radar Lite local product — 2026-10-08（Radar 精简版本地产品）

- Product entry（产品入口）: `radar_product.py` — one local process serves Dashboard + API + bounded Lite actions.
- Product flow（产品链路）: **Start → Run Lite Scan → inspect Top Opportunities / Evidence → Generate HANDOFF.json**.
- Action API（动作接口）: `POST /v1/lite/scan` and `POST /v1/lite/handoff`; both are localhost-only when product actions are enabled.
- Compatibility（兼容）: existing GET/read API remains unchanged; unrelated write endpoints still return 405.
- Handoff safety（交接安全）: output path is server-generated under `.radar/lite/handoffs/`; client cannot choose an arbitrary output path.
- User guide（用户指南）: `RADAR_LITE_USER_GUIDE.md`.
- Focused product validation（产品专项验收）: **16/16 PASS（通过）** + `node --check dashboard/app.js` PASS.
- Full Radar regression（Radar 全量回归）: **181/181 PASS（通过）**.
- Real local E2E（真实本机端到端）: **PASS** — Dashboard served successfully; native Lite Scan run `c378b1bef88a53bafedc78c2` returned 3 opportunity cards; rank #1 = `agent built`; deterministic Handoff creation succeeded.
- Real Handoff evidence（真实交接证据）: `.radar/lite/product-e2e-handoffs/c378b1bef88a53bafedc78c2/rank-01-agent-built/HANDOFF.json`, schema = `radar-ai-team-handoff-v1`.
- Product boundary（产品边界）: GSC / AIHOT / China-social / Google Ads / extra Providers / new scoring remain **Deferred / Restartable（延后 / 可重启）**, not removed.
- AI Team system regression after Goal Runner + Synthesis budget hardening（AI Team 系统回归）: **123/123 PASS（通过）**.
- Historical AI Team productization run `run_670842b39988316bcf42` remains failed truthfully due Worker health / synthesis-budget history; the local Radar product was completed and independently validated directly rather than rewriting that Run as success.

### Multilingual Radar — 2026-10-08（多语言 Radar）

- Dashboard Language（语言） selector: **implemented**.
- First-class scan options（一级扫描选项）: 中文 `zh/CN`, English `en/US`, 日本語 `ja/JP`, 한국어 `ko/KR`, Français `fr/FR`, Deutsch `de/DE`, Español `es/ES`, Português `pt/BR`.
- Scan semantics（扫描语义）: selected language + country/region are passed into the existing native Lite discovery chain; this is not a cosmetic UI-only switch.
- Full Dashboard i18n（完整面板国际化）: **implemented** via `dashboard/i18n.js`; static shell + dynamic cards + Provider status + score labels + opportunity detail + evidence/history + Research Pack + action/error states + date locale all switch with language.
- All 8 language packs（8 个语言包） expose the same complete UI key set; automated regression fails if a language pack is missing a UI key.
- Source data（源数据） remains authoritative in its original language; UI chrome（界面文案） is translated, and visible natural-language content can now be presentation-translated into the selected Radar language without overwriting the source record.
- HTTP product validation（HTTP 产品验收）: selector contains all 8 options; `zh/CN`, `ja/JP`, `ko/KR` action requests all PASS.
- Content Translation Layer（内容翻译层）: opportunity titles/reasons, Provider warnings, detail titles, Why now, Evidence titles/snippets, and Action Card values automatically follow the selected Radar language.
- Translation backend（翻译后端）: reuses the existing local AI Team Qwen credential source + qwen3.7-plus; no new key is embedded in Radar.
- Translation cache（翻译缓存）: persistent local SQLite cache keyed by model + target language + source text; repeated content avoids repeated model calls.
- Translation failure mode（翻译失败模式）: silently falls back to the original text and never blocks Scan / Inspect / Handoff.
- Live translation verification（真实翻译验证）: English → Chinese returned “AI 智能体正变得更容易构建和复用。”; immediate repeat returned cache hit with zero provider translations.
- Full Radar regression after content translation（内容翻译后全量回归）: **190/190 PASS（通过）**.

### AIHOT standalone comparison — 2026-10-08（AIHOT 独立对比部署）

- Official upstream（官方上游）: `KKKKhazix/AIHOT`, deployed under `external/aihot-compare`.
- Isolation（隔离）: standalone Docker Compose stack; **not** promoted into Radar native discovery and **not** allowed to alter Radar scores automatically.
- Local URL（本地地址）: `http://127.0.0.1:3000`.
- Runtime（运行态）: PostgreSQL healthy; API / Web / Worker running; homepage HTTP 200; Admin route available.
- LLM（模型）: reuses the existing local AI Team Qwen credential source and `qwen3.7-plus` OpenAI-compatible endpoint; key value is not copied into documentation or logs.
- Radar comparison URL（Radar 对比地址）: `http://127.0.0.1:8787`.
- Next comparison rule（下一比较规则）: compare product usefulness / signal quality / evidence transparency on matched scopes before any provider-integration decision; deployment alone does not justify promotion.

### R3 corrected decision model — 2026-10-06（R3 纠正后的决策模型）

- Legacy R3 4-gate checklist（旧 R3 四关卡清单）保留历史，不再作为单一产品生死门。
- Discovery Quality（发现质量）: **SUPPORTED WITH PROTOCOL MISMATCH** — 25/25 真人判断完成；旧 binary 口径为 24 labels / 22 unique topics / micro precision@5 = 0.90；1 个真实 weak（弱相关）不强行二值化。
- Handoff Value（交接价值）: **PASS** — Agent Skills 端到端交接通过；HANDOFF.json → Product Contract 标准静态接入 **2/2 PASS**.
- Real Demand / First-party Value（真实需求 / 第一方价值）: **EXTERNALLY BLOCKED** — 等待真实 owned/authorized GSC property.
- Source Expansion（扩源）: **DEFERRED** — Native vs AIHOT / China-social 暂不继续花费。
- Feature policy（功能策略）: **LITE-FIRST / EXPANSION FREEZE** — 先使用精简版；Provider / 模型 / UI / scoring 外扩由真实需求或真实交付缺口触发，并可按 `RADAR_EXTENSIONS.json` 重启。

- Chinese/English focused product tests: **38/38 PASS** after Dogfood 005.
- Full Radar regression: **172/172 PASS（通过）**.
- Agentic scope gating: scope "AI" and "AI Agents" now include GPT-6.1 Sol for event a59d9623107f219540f6c0e1; unrelated Chinese/English noise stays blocked.
- Acceptance harness A1–A6: PASS.
- English parity: 0 failures across four scopes.
- Dogfood 005 Repair（修复）→ Independent Revalidation（独立复验） verdict: `accept（通过）`.

## Known non-blocking risk

The known action-verb compound truncation risk now has concrete regression coverage. Future Chinese segmentation changes must preserve full-compound coverage, bad-fragment suppression, clause boundaries, source-span provenance, and English parity.

### R3 validation infrastructure — 2026-10-04

- `r3_validation.py`（R3 验证批次）enforces >=20 human labels, >=20 unique labeled topics, complete Top-5 labels and micro precision@5 >= 0.60.
- `r3_label_pack.py`（R3 人工标注包）now builds a pre-label evidence-sufficiency batch with **5 complete scopes / 25 label slots / 23 unique topics**; scope selection is recorded as `label_dependent=false（不依赖人工标签）`.
- Current human evidence（当前真人证据）: **25/25 真人判断已完成**。冻结的 binary gate 当前可计 **24 labels / 22 unique topics**，micro precision@5 = **0.90**；数量、去重主题、精度三项均过线，但 all_top_k_complete=false，原因是 AI Engineering #4 = **弱相关**。明确 graded relevance（分级相关性）样本现有 **11 个**，总体均值 **0.7879**；旧“靠谱/不靠谱”不会被事后伪造成强/中/弱相关。model/self/synthetic labels explicitly do not count.
- Scope collapse（范围塌缩）was reproduced from 119 stored real events: compound AI scopes previously matched on generic `AI` alone.
- Scope specificity（范围特异性）was hardened: compound scopes such as `AI Safety` / `AI Coding` must match a specific scope token; pure `AI` behavior and controlled `agentic/智能体` aliases are preserved.
- Post-fix Top-20 overlap vs `AI Agents`: AI Coding 0.143, LLM Evaluation 0.250, AI Infrastructure 0.026, AI Safety 0.038, AI Search 0.081, AI Productivity 0.000.
- Mixed/AIHOT batch experiments exceeded the 120s budget and produced no persisted evaluation DB; this is recorded as validation evidence rather than hidden or retried indefinitely.
- Human label artifacts: `.radar/r3/human-label-pack/batch.json`, `LABEL_SHEET.md`, `result.json`, `R3_VALIDATION_REPORT.md`.

### R3 external-input readiness — 2026-10-04（R3 外部输入就绪度）

- Human precision gate（人工精度门槛）: **25/25 human judgments（真人判断）已完成**；其中 **11 个具备明确 graded relevance（分级相关性）**。frozen binary gate currently counts **24 labels / 22 unique topics**, micro precision@5 = **0.90**；label-count / unique-topic / precision 子门槛均已通过，当前只剩 all_top_k_complete 因 1 个真实“弱相关”判断而未通过。
- GSC first-party validation（GSC 第一方验证）: **externally_blocked（外部阻塞）**; validator ready, current real state is **0 workspace / no GSC token / 0 registered properties**. Owner: `verified-property owner / external input`; recorded 2026-10-06. Do not spend model/engineering cycles re-checking until a real authorized property exists.
- Design Partner session（设计伙伴验证会）: 真人最终澄清 **Top-5 全部 actionable=false（都不知道从哪里开始，完全懵）**。此前 ranks 1–3 的“能行动”解释已被这一更明确的后续反馈纠正；would_act（是否愿意做）仍保留用户原先明确表达，不用 actionability（可行动性）去倒推。
- Product finding（产品发现）: relevance（相关性）不能代表 opportunity value（机会价值），而“主题 + 趋势证据”也不能代表 actionable（可行动）。真实用户需要明确的 first step（第一步）、minimum viable output（最小成品）、effort / risk（投入 / 风险）和 stop rule（放弃条件）；否则即使 usefulness（有用性）高，也仍然无法开始。
- Repair action（修复动作）: 在继续 Native vs AIHOT（原生 vs AIHOT）之前，先用当前 Top-5 生成一版 Action Card（行动卡）并做同一真人复验，验证“看完是否知道第一步做什么”。
- Action Card implementation（行动卡实现）: **complete（完成）**。Research Pack（研究包）现自动输出 30 分钟 first step（第一步）、minimum viable output（最小成品）、starter risk（起步风险）和 stop rule（停止条件）；历史 Research Pack 在读取时向后兼容补齐，不重写数据库快照。
- Live verification（真实数据验证）: 当前 AI Agents（AI 智能体）真实 Top-5 **5/5 均能生成 Action Card**；Dashboard（面板）已展示行动卡。修复后全量 Radar regression（回归）: **170/170 PASS（通过）**。
- Remaining Design Partner gate（设计伙伴剩余关卡）: 只剩真人 repair revalidation（修复后复验）——确认看完 Action Card 后是否知道第一步怎么开始；在真人确认前不把该 Gate 标成通过。
- Native vs AIHOT experimental comparison（原生 vs AIHOT 实验对照）: experimental Top-5 generated from real captured observations; human labels pending; `automatic_provider_promotion=false`.
- Deterministic HTML entity bug（HTML 实体解析缺陷） fixed: literal `&nbsp;` can no longer create fake `nbsp nbsp` candidates.
- Full regression after all R3 prep + candidate-scope/performance hardening: **163/163 PASS（通过）**.

- Local human-label UI（本地人工标注界面）: `r3_label_server.py` binds only to `127.0.0.1`, never calls a model, persists only human label fields, and passed a real Browser Worker read-only smoke test.
- R3 readiness control plane（R3 就绪度控制面）: `.radar/r3/R3_READINESS.json` currently reports `preparation_complete=true`, `stage_progress=0/4`, `r3_complete=false`.

### Candidate discovery performance hardening — 2026-10-04（候选发现性能加固）

- Root cause（根因）: deterministic clustering / event assignment repeatedly normalized and tokenized identical event/candidate strings millions of times.
- Fix（修复）: bounded LRU cache（有界最近最少使用缓存） on pure `normalize_text` and `canonical_tokens`; cache limits are 16,384 and 32,768 entries.
- Behavior rules unchanged（行为规则未改变）: no scoring, ranking, scope, alias or filtering formula was modified by this optimization.
- `AI Agents` 119-event discovery: **26.339s → 0.528s** (~50x faster).
- `AI Safety`: **2.244s → 0.019s**.
- Seven-scope offline pass: **0.758s total**.
- Full Radar regression: **163/163 PASS（通过）**.


### Candidate-level scope leakage rescue — 2026-10-04（候选级范围泄漏修复）

- Root cause（根因）: `_scope_supported()` gated the parent event（父事件）only; once the event entered the pool, unrelated GitHub topics / extracted phrases（GitHub 主题 / 提取短语）could leak into ranking.
- Concrete symptom（具体症状）: `AI Coding（AI 编程）` included `job search（求职搜索）` and `video production（视频制作）`; `AI Search（AI 搜索）` could also inherit job-search ambiguity（求职搜索歧义）.
- Fix（修复）: deterministic candidate-level scope gate（确定性候选级范围门槛） using controlled semantic families（受控语义族）, with no LLM / embedding service（大模型 / 向量服务） and no product-name whitelist（产品名白名单）.
- Compatibility（兼容）: preserves `Claude Code`, `coding agent（编程智能体）`, real `GPT-6.1 Sol` agentic evidence（智能体证据）, Chinese discovery safeguards（中文发现保护）, and first-party GSC explicit-query escape behavior（第一方 GSC 显式查询逃生行为）.
- R3 pack hardening（R3 标注包加固）: fixed pre-label scope pool（标注前固定范围池） + evidence sufficiency（证据充足度）; underfilled Top-5 scopes are skipped before labels exist, and generation fails closed if <20 unique topics（不足 20 个唯一主题则失败关闭）.
- Independent validation（独立验证）: DSH executed the fixed validation harness itself — focused 4/4, grouped 41/41, full **163/163 PASS（通过）**, pack truth **5 queues / 25 slots / 23 unique / 0 labels**, diff check PASS; final `VERDICT: accept（结论：通过）`.
- Process truth（过程真相）: the original formal Supervisor Run（正式主管运行）`run_61405e43b06fa948779d` remains `blocked（阻塞）`; the later rescue is recorded separately and does not rewrite history.

- Current Design Partner session（当前设计伙伴验证会） from real Radar run `36f2958873957e3d830de79f`: `AI Agents（AI 智能体）` Top-5 = agent skills / multi agent / autonomous ai / AI Agents / agent build; ranks 1–3 fully answered，rank 4 usefulness = **5/5**，rank 5 pending.
- Native vs AIHOT comparison（原生 vs AIHOT 对照） regenerated after candidate-scope hardening: AIHOT Top-5 remains complete, `human_labels=0`, `promotion_allowed=false`, comparison=`pending_human_labels（等待人工标签）`.
- GSC Wizard（GSC 向导） account check: Google account + `webmasters` scope（站长工具授权范围）connected, but GSC Wizard currently has **0 registered properties（0 个已注册站点属性）**; a real property URL is still required before first-party validation can run. Local Radar GSC workspace/token truth remains unchanged.

### Candidate Scope Gate hardening — 2026-10-04（候选级范围门槛加固）

- Root cause（根因）: event-level scope gating（事件级范围过滤）只能证明整条来源事件相关，不能证明事件中抽取的每个候选短语都相关。
- Fix（修复）: `discovery.py::_candidate_scope_supported` 对 GSC 多词查询、GitHub topics（GitHub 主题标签）和标题/中文短语做候选级二次范围检查；规则是确定性的，不调用 LLM（大模型）。
- AI Coding（AI 编程）真实 stored-event Top-5（已存真实事件前五）从 `claude code / agentic ai / agentic framework / generative ai / prompt engineering` 收敛为 `claude code / coding agent / context engineering / prompt engineering / engineering skill`.
- AI Agents（AI 智能体）收敛为 `agentic ai / agentic framework / agent going / agentic workflow / agent skills`.
- Recall risk（召回风险）: AI Safety / AI Infrastructure / AI Search（AI 安全 / AI 基础设施 / AI 搜索）当前分别只有 3 / 1 / 1 个候选进入 Top-5 比较，说明 precision（精度）提升伴随 recall（召回率）下降；R3 不应通过放宽门槛来掩盖此风险，应继续用真实人工标签验证。
- Independent validation（独立验证）: focused discovery **19/19 PASS（通过）**; full Radar regression **165/165 PASS（通过）**.
- Canonical human-label pack（正式人工标注包）: **25 slots / 23 unique topics / 0 human labels（25 个位置 / 23 个去重主题 / 0 个人工标签）**，所有 `relevant` 仍为 `null`，未伪造人工评价。
- Before/after evidence（前后对比证据）: `.radar/r3/candidate-scope-before-after.json`.

### Human validation UX hardening — 2026-10-05（人工验收交互加固）

- Local label UI（本地标注界面）now accepts **靠谱 / 一般或不确定 / 不靠谱** instead of forcing an opaque yes/no choice.
- Truth rule（真相规则）: 靠谱 -> `relevant=true`; 不靠谱 -> `relevant=false`; 一般/不确定 -> 保存 `human_judgment=uncertain` 且保持 `relevant=null`，因此不会被强行计入 precision（精度）.
- Backward compatibility（向后兼容）: existing `relevant=true/false` API requests remain accepted.
- Full Radar regression after the UX change: **166/166 PASS（通过）**.
- GSC Wizard account truth（GSC 账户事实）: Google authorization + `webmasters` scope 已连接，但当前账号可见 Search Console property（站点属性）仍为 **0**；此项继续作为外部输入阻塞，不伪造完成。

### Radar V2 Milestone 1 — persistence migration（持久化迁移）— 2026-10-11

- Scope（范围）: 按 `RADAR_V2_DESIGN.md` + `.ai-team-runs/radar-v2-active/qwen-interface/RADAR_V2_IMPLEMENTATION_PLAN.md` §2 实现**第一可部署切片**的持久化层。
- Deliverable（交付物）: 新增 `tracker.py`（`TrackerStore`）与 `test_tracker.py`；**未修改任何 V1 源码，未新增/修改任何 V1 表或索引**。
- Schema（表结构）: 4 张 V2 表落库，全部 `CREATE TABLE IF NOT EXISTS`，与 V1 同库不同表 — `tracker_schedules` / `tracker_runs` / `tracker_diffs` / `tracker_alerts`，含 5 个配套索引。外键仅引用 `workspaces(workspace_id)`；**刻意不引用** `radar_runs` / `opportunity_snapshots`，规避级联风险。
- Determinism（确定性）: 所有 ID 复用 `core.stable_id()`，时间戳复用 `core.utcnow()/isoformat()`；`diff_id = stable_id("tracker-diff", prev_run, curr_run, topic_id, change_type)`，`alert_id = stable_id("tracker-alert", diff_id, alert_type)`，因此**重复写入幂等**。
- Isolation（隔离）: `TrackerStore` 沿用 `OpportunityReadStore` 同构连接约定（WAL + `foreign_keys=ON` + `sqlite3.Row`），支持 `read_only=True` 只读连接，只读库拒绝初始化 schema。
- Verification（验证）: `python3 -m unittest test_tracker` → **28/28 PASS**；全量回归 `unittest discover -p 'test_*.py'` → **219/219 PASS（通过）**（基线 191 + 新增 28），零回退。
- Data safety（数据安全）: 测试全部使用 `tempfile` 临时库；真实 `.radar/radar.db` SHA-256 在回归前后一致（`1bd03ddfd15530ac279404b184b6523323673815d46dba8b43adbd50421a62bb`），未被触碰。
- Not yet done（尚未完成，交后续里程碑）: `TrackerScheduler`（cron 解析 + tick）、`ChangeDetector`（读库版，可复用 `.ai-team-runs/radar-v2-active/dsh-core/radar_v2_diff.py` 的已验证纯函数逻辑）、`AlertDispatcher`、`tracker_api.py` 的 `/v2/` 只读路由。
- Process truth（过程真相）: 本次改动**未 git commit**；仓库仍保留其他 Agent 的未提交改动。

### Radar V2 P2 — production runtime integration（生产运行接线）— 2026-11-11

- P0/P1（安全与备份）: 对当前项目文件执行自定义密钥/隐私模式扫描；本地环境配置与运行时状态按 .gitignore 排除，机器绝对路径仅在备份副本中替换，原文件未改；独立快照分支 backup/ai-team-skills-2026-11-11-snapshot 已推送并核验远端 SHA，开发分支未提交、未切换。
- Runtime（运行适配）: 新增 tracker_runtime.py，将 V2 scheduler 接入现有 run_discovery()；只使用 GitHub / Hacker News / Google News / GDELT 免费公开源，不传 GSC、Google Ads、网站深抓取或付费翻译凭据。
- Persistence（持久化）: 启动时幂等初始化 4 张 V2 表，为启用工作区建立默认关闭的 6 小时 schedule；只有本机显式启用 schedule 或调用 run 接口才会触发采集；本机 POST /v2/workspaces/{workspace_id}/run 可人工触发。
- Baseline（基线）: 首次 V2 run 可绑定同一 workspace/scope 的有效 V1 历史快照；之后严格沿用上一条成功 V2 run，拒绝跨 workspace 或不完整快照。
- Diff / Alert（变化与提醒）: Diff 按 workspace 可查询并持久化；常规提醒仅在 magnitude ≥ 10 时生成，duplicate_id 作为数据完整性信号例外；提醒只存储，不发送邮件、Webhook 或第三方通知。
- API（接口）: 新增 /v2/health、/v2/workspaces、workspace 下的 schedules/runs/diffs/alerts 查询；POST schedule/run/tick 仅允许 localhost。
- Verification（验证）: Radar 全量 Python 回归 /opt/homebrew/bin/python3 -m unittest discover -p 'test_*.py' -q → 381/381 PASS；新增 runtime/API/基线与低幅度告警阈值测试，测试均使用临时 SQLite 和模拟扫描，不触发真实外部采集。
- Production gate（生产门禁）: 真实 .radar/radar.db 已先做 SQLite 一致性备份；V2 schema 已初始化，8787 服务已重启，/v2/health 已返回 ok。默认 schedule 保持关闭，首次真实采集需用户明确触发。
