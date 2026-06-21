# BeatOdds Long-Running Task Context

This file is the persistent working context for future agents. Update it whenever
a milestone is completed, a plan changes, or a new operational constraint is
discovered.

## Communication Contract

- Be brief and direct.
- Preserve user changes. Do not revert unrelated work.
- Treat this as a long-running research engineering project, not a one-shot script.
- Before starting a substantial step, read this file, `README.md`, and current
  `git status --short --branch`.
- Do not commit `.env`, `data/`, `.venv/`, caches, or full external reference
  repository mirrors.

## Project Objective

The target in `../BeatOdds_midterm.pdf` sections 3 and 4 is a Polymarket
mispricing agent:

```text
edge = p_f - p_m
```

- `p_m`: current Polymarket market probability from live order books.
- `p_f`: BeatOdds fair probability estimate from evidence, market structure, and
  resolution semantics.
- The system should find, rank, track, and evaluate contracts where `p_f`
  materially differs from `p_m`.

The current implementation strategy is live forward evaluation. A fully clean
offline historical dataset is expensive because historical evidence availability
is hard to reconstruct. Live collection avoids leakage by freezing evidence at
prediction time and appending later outcomes.

## Current State

As of 2026-05-25:

- GitHub remote: `https://github.com/hu-jy23/beatodds.git`, private.
- Base committed state on `master`: initial BeatOdds prototype.
- As of 2026-05-31, `ref/` reference repositories are real git submodules
  using SSH GitHub URLs in `.gitmodules`; initialize fresh clones with
  `git submodule update --init --recursive`.
- `py-clob-client-v2` is resolved from
  `ref/official-polymarket/py-clob-client-v2` in `pyproject.toml`.
- Submodule conversion was verified in the `odds` conda env with
  `uv sync`, `uv sync --extra dev`, `uv run ruff check .`, and
  `uv run pytest -q`.
- As of 2026-05-31, the missing `src/beatodds/data/` live data layer was
  restored with Gamma API reads, CLOB v2 snapshots, and an incremental market
  indexer. `.gitignore` now ignores only root `/data/` so source code under
  `src/beatodds/data/` remains trackable.
- A README full pipeline run in the `odds` conda env succeeded:
  `backfill_markets.py --incremental`, `run_scanner.py --top 10`,
  `run_scanner.py --top 10 --complete-groups`, and
  `run_forecast.py --top 5`.
- A local GUI now lives under root `gui/` and is launched with
  `uv run scripts/run_gui.py --host 127.0.0.1 --port 8765`. It reads local
  DuckDB files, serves a three-column collapsible operator console, and stores
  GUI memory/history in ignored `data/gui_state.json`.
- GUI scroll behavior is rail-contained: page/body scrolling is disabled and
  left, middle, and right columns scroll independently. Middle actions now
  persist observable state for tracked markets, follow-ups, reviews, generated
  message drafts, and simulated paper deals.
- As of 2026-05-31, GUI action state is topic-scoped: dialog/action logs,
  generated messages, simulated paper deals, follow-ups, and reviews are
  separated per selected market and can be cleared per topic or globally.
  Aggregate Tracking Console, Action Report, Generated Messages, and Special
  Report panels live in the right rail; the middle rail is reserved for the
  selected topic's analysis, current-topic brief, and notes.
- GUI rail ownership was refined on 2026-05-31: left rail is the topic list,
  middle rail is selected-topic information including analysis, live price,
  statistics, evidence, and notes, and the wider right rail is aggregate console
  state including metric strip, operator summary, reports, and generated
  messages. Chart sizing uses fixed logical canvas heights to avoid growth when
  switching topics.
- As of 2026-06-03, the GUI is event-centric to match Polymarket's event →
  market hierarchy. `data/beatodds.duckdb` now has an `events` table and
  `markets.event_id`; the left rail lists events, the middle rail shows selected
  event detail plus markets in that event, and the right rail is the selected
  market workbench. `/api/state` returns the event shell without blocking on
  CLOB; live quote details load separately through `/api/market/<condition_id>`.
- As of 2026-06-03, the GUI middle event page follows Polymarket's event layout
  more closely: event icon, tags, title/meta, market cards with prominent
  YES/NO token buttons, and a compact rules/background tab. The UI has a
  Dark/Light toggle; dark mode uses a black Polymarket-like theme.
- As of 2026-06-03, the right-rail Live Price panel includes a scrollable CLOB
  order book for the selected YES/NO token side. It displays all returned ask
  and bid levels as price, shares, and total; cumulative depth curves are not
  drawn.
- Local market schema now stores `events.image`, `events.icon`, and
  `markets.outcome_prices_json`. Selecting a market side persists
  `selected_side` in `data/gui_state.json`; `/api/market/<condition_id>?side=NO`
  reads the NO token order book and converts forecast/chart probabilities to
  the NO side.
- A paper trading workflow discussion deck now lives at
  `docs/paper_trading_workflow_ppt.html`. It frames paper trading as a
  long-horizon ledger with accounts, orders, fills, positions, marks, exits,
  settlements, and discussion points for future implementation.
- As of 2026-06-04, paper trading account infrastructure is implemented in
  `src/beatodds/evaluation/paper_store.py`. It creates `paper_accounts` and
  `paper_account_transactions` in `data/eval.duckdb`, supports default demo
  account creation, account risk parameter updates, deposit/withdraw,
  reserve/release cash, account summaries, and transaction history. CLI entry:
  `uv run scripts/run_paper_account.py --create-default`.
- As of 2026-06-05, account UI was moved out of the market page. The global top
  bar switches between the Markets page and a separate User page. The User page
  has its own sidebar for Overview, profile settings, funding, positions/trade
  history, and Agent Pattern config. Login lists local `paper_accounts` and
  switches accounts without passwords; creating a user only requires a name.
  Funding writes `paper_account_transactions`. Agent Pattern writes sizing
  mode, order fraction, fee/slippage, exposure limits, cash buffer, and the
  autonomous paper order switch back to `paper_accounts`. Default sizing is
  all-in per trade with 0 fee/slippage. Simulated GUI paper deals read the
  selected account config to estimate notional, shares, fees, and projected
  PnL; positions/trades currently aggregate those simulated paper deals until
  formal `paper_orders`, `paper_fills`, and `paper_positions` exist. Future
  account features should extend the User page instead of crowding the market
  event page.
- As of 2026-06-05, China-specific info search M1/M2/M3/M4/M7 has a first
  implementation. `ResolutionFeatures` now stores `event_type`,
  `china_relevance`, `geography`, `resolution_source_hint`, and
  `source_routing_hints`. `src/beatodds/evidence/china_query.py` generates
  deterministic Chinese baseline/official/site queries. `china_sources.py` and
  `china_router.py` load `configs/china_sources.json` and route China-related
  markets to official/regulator/exchange/company-filing domains. The retriever
  now uses provider abstraction under `src/beatodds/evidence/providers/`;
  Tavily remains the default provider and `MockSearchProvider` supports tests.
  `--china-info` is available in `scripts/run_forecast.py` and
  `scripts/run_batch_eval.py`; it only adds China routing when
  `china_relevance != low`. `workflow_evidence_items` now persists provider,
  source type, direction, strength, resolution relevance, reliability prior,
  dedupe key, and raw metadata.
- As of 2026-06-05, forecast workflow replay artifacts are implemented.
  `src/beatodds/evaluation/workflow_records.py` writes a JSON and Markdown copy
  for every `save_forecast_run()` call. Default output directory is
  `workflow_records/`, and generated run artifacts are ignored by
  `workflow_records/.gitignore`; only the directory README/ignore rules should
  be committed. Use `WORKFLOW_RECORDS_DIR` to redirect local artifacts.
- As of 2026-06-09, China-specific agentic harness MVP work has started.
  `src/beatodds/agents/` now contains file-workspace, trajectory, source-card,
  and access-tool registry primitives. `scripts/run_china_harness.py` can create
  `workspace/china_forecasts/{event_slug}/{market_slug}/{agent_run_id}/`, run
  mock or Tavily-backed search, and save search actions plus source cards.
  `workspace/` is ignored because it contains local run artifacts.
- As of 2026-06-09 later in the same milestone, Phase 1 through Phase 4
  validation was wired for the China harness. Access tools include
  source-registry export, China query generation, Polymarket context,
  search_web, resource processor stub, and market-anchored model baseline stub.
  `ChinaAgentLoopController`, `ScriptedChinaAgent`, and `LLMChinaAgent` remain
  available as legacy validation/API-agent paths.
- As of 2026-06-10, China-specific harness architecture moved to a Markdown-first
  local-agent model. `scripts/run_china_harness.py` now creates a workspace plus
  `task.md`, `tool_manifest.md/json`, and `codex_prompt.md`; it does not start a
  DeepSeek/OpenAI API agent as the main loop. The intended main forecaster is a
  local Codex agent, currently documented as `codex:gpt-5.4-mini`, reading
  `task.md` and maintaining `plan.md`, `trajectory.md`, `claims.md`, `audit.md`,
  `forecast_report.md`, `forecast_report.json`, and `forecast_report.pdf`.
- As of 2026-06-10, `scripts/china_harness_tool.py` is the local-agent tool
  bridge. A Codex agent can call repo tools such as `read_polymarket_context`,
  `export_source_registry`, `generate_china_queries`, `search_web`,
  `process_resource`, and `model_baseline_forecast`; each call persists
  `search_actions/`, source cards, compact claims, and a trajectory step in the
  existing workspace. `model_baseline_forecast` stays market-anchored by default;
  DeepSeek/OpenAI-compatible baseline calls require explicit
  `--enable-llm-baseline` and are not the main agent loop.
- China harness artifact audit still requires `trajectory.md` to show
  `Evidence k -> analysis k -> Search k+1 -> Evidence k+1`. Existing fixes from
  earlier API-agent audits remain: compact claims, prediction-market
  self-reference filtering, source-category social/video guards, Xi leadership
  query template fix, URL support for resource processing, source-quality
  scoring, context-entity checks, boilerplate detection, and result reranking.
  Audit notes live in `docs/china_harness_audit_log.md`; detailed test plan lives
  in `docs/china_harness_test_plan.md`.
- As of 2026-06-12, the China-specific Markdown-first harness has a
  future-oriented exploration validation baseline. `task.md` and
  `tool_manifest.md` can now be generated for `codex:gpt-5.4` via
  `--agent-model codex:gpt-5.4`; `scripts/audit_china_harness_run.py` checks
  required files, prediction-oriented audit fields, trajectory appendix,
  evidence path counts, and a 7-part rubric for time perspective, search
  branches, query design, trajectory causality, forward-looking sources,
  counterevidence, and stopping conditions.
- Three `gpt-5.4` validation reports passed the audit on 2026-06-12:
  Best Chinese AI Company end of July (`13/14`, top pick Alibaba `p_f=0.33`,
  `confidence=0.52`), Taiwan invasion by end of 2026 (`12/14`, `p_f=0.10`,
  `confidence=0.62`), and Xi out as CCP General Secretary before 2027
  (`11/14`, `p_f=0.07`, `confidence=0.78`). Reports live under
  `workspace/*/*/gpt-5.4/forecast_report.{md,json,tex,pdf}` and remain ignored
  local artifacts.
- Operational lesson from the 2026-06-12 Best AI run: video/ASR resource
  processing can stall a main agent. Future runs should treat unfinished video
  parsing as an auditable coverage gap after a bounded wait, then proceed to
  synthesis. Tool dry-runs must use separate validation workspaces; do not run
  manual tool checks inside a formal forecast workspace.
- As of 2026-06-13, the top-level main-agent validation run
  `workspace/will_china_invade_taiwan_by/2026/gpt-5.4-top-cookie-bili-20260613/`
  passed after a full specialty check. The run produced
  `capability_check.md`, verified that a top-level subagent could be launched,
  confirmed `data/secrets/www.bilibili.com_cookies.txt` was actually used in
  `yt-dlp` commands, deep-processed the high-value Bilibili video
  `《【兵棋推演】2027台海特种行动 第一集：剧本背景及作战准备》（BV1JnxwetEUw）`,
  generated its `video_report.pdf`, and verified via
  `sync_resource_status --all` plus `render_status_audit.md` that
  `resource_processor.json` reached `processor_status=video_render_complete`,
  `content_access.video_body_status=complete_report`, and
  `render.render_status=complete`.
- The same 2026-06-13 validation run rendered
  `forecast_report.pdf`, passed `scripts/audit_china_harness_run.py` with
  rubric total `18`, and ended with machine-readable forecast
  `p_f=0.010`, `p_m=0.068`, `p_m_delta=-0.058`, `confidence=0.83`,
  `calibration_status=uncalibrated`,
  `mispricing_verdict=absolute_overestimate`,
  `paper_trade_view.direction=buy_no`.
- As of 2026-06-13, video render uses an explicit worker/ASR lock protocol.
  `process_resource` render contracts include `video_render.lock.json` and
  `asr.lock.json`; video workers must create/update these locks around
  download/render and Whisper/ASR work. `sync_resource_status` detects active
  locks and marks the resource `video_render_in_progress` with
  `render_status=in_progress`, `content_access.video_body_status=render_in_progress`,
  and `content_access.asr_in_progress=true`. Main agents must not start a
  duplicate `ffmpeg` or Whisper run while an active lock or live worker process
  exists. If a lock is stale, record lock path, mtime, process status, and
  takeover reason before rerunning ASR.
- As of 2026-06-12, video resource processing exposes a subagent-ready render
  contract. `process_resource` now writes `render_request.json`,
  `video_report_prompt.md`, `subagent_spawn_prompt.md`, and `artifact_index.md`.
  The contract names the render skill, local `SKILL.md` path,
  `gpt-5.4-mini` worker model, `multi_agent_v1.spawn_agent` args, bounded wait
  policy, output scope, and completion checks. `task.md` and
  `tool_manifest.md/json` instruct a `gpt-5.4` main agent to pass
  `bilibili-render-pdf` / `youtube-render-pdf` as `items[type=skill]` to the
  worker when `render_status=required`.
- As of 2026-06-13 later, Xi 2027 rerun workspace
  `workspace/xi_jinping_out_before/2027/gpt-5.4-lock-rerun-1/` completed a
  top-level `codex:gpt-5.4` forecast and passed
  `scripts/audit_china_harness_run.py` with rubric total `18`. Evidence-first
  forecast ended at `p_f=0.015` versus `p_m=0.08`, with
  `mispricing_verdict=absolute_overestimate` and
  `paper_trade_view.direction=buy_no`. The run also verified lock handling in
  practice: a `gpt-5.4-mini` worker processing
  `https://www.youtube.com/watch?v=Yi5jGG_Dtug` created active
  `video_render.lock.json` and `asr.lock.json`, `sync_resource_status --all`
  reported the resource `in_progress`, and the main agent did not start a
  duplicate download or ASR pass while `yt-dlp` / `whisper` were live.
- New operational lesson from the 2026-06-14 CLI x2 run: a spawned video worker
  can miswrite into a sibling workspace instead of the current run's
  `output_dir`. Before assuming a resource is idle, check `ps`, `cwd`, and
  sibling workspace lock files for the same video id. If the foreign worker is
  still active, do not start duplicate ASR. After it finishes, copy only local
  body materials such as `mp4`, `transcript.srt`, or `video_metadata.json` into
  the current workspace and run `finalize_video_report` there. Also note that
  `scripts/china_harness_tool.py finalize_video_report` is safer with an
  absolute `--resource-dir`; relative paths can fail the
  `resource_processor.json` existence check.
- As of 2026-06-13 night, the Xi 2027 lock rerun validation finished as a
  `3 x codex:gpt-5.4` ensemble:
  `workspace/xi_jinping_out_before/2027/gpt-5.4-lock-rerun-1/`
  (`p_f=0.015`, audit `18`),
  `workspace/xi_jinping_out_before/2027/gpt-5.4-lock-rerun-2/`
  (`p_f=0.040`, audit `16`), and
  `workspace/xi_jinping_out_before/2027/gpt-5.4-lock-rerun-3/`
  (`p_f=0.020`, audit `16`). All three produced
  `forecast_report.pdf`, `forecast_report.json`, `full_trajectory.md`,
  `thesis_review.md`, and passed hard-gate audit checks. All three concluded
  `buy_no` against `p_m=0.08`; the summary mean is `p_f≈0.025`,
  `p_m_delta≈-0.055`.
- The Xi 2027 three-run summary lives at
  `workspace/xi_jinping_out_before/2027/gpt-5.4-lock-rerun-summary/`. It was
  generated by `scripts/summarize_china_parallel_runs.py` and rendered with
  `scripts/render_forecast_report_pdf.py`. The PDF includes the three-agent
  field table, artifact/audit table, video processing state, consensus, and
  conflicts. Main consensus: resolution is narrow, ordinary organization
  timing does not support a 2026 formal exit, and remaining YES probability is
  mainly health/core-rupture/abnormal-organization tail risk. The initial
  summary recorded incomplete video render/ASR as a defect; this was later
  fixed by the video finalizer path described below.
- As of 2026-06-13 late night, the Xi 2027 video coverage-gap defect was fixed
  and validated on the three long-video resources from the lock rerun ensemble.
  `src/beatodds/agents/access_tools.py` now treats dead-PID locks as stale and
  detects arbitrary `*.srt` transcript artifacts. `src/beatodds/agents/video_reporter.py`
  and `scripts/finalize_video_resource_report.py` provide a deterministic
  fallback that downloads missing video when needed, runs Whisper small when no
  subtitle exists, writes `video_parse_report.md`, `evidence_card.md`,
  `claims.jsonl`, `artifact_index.md`, and compiles `video_report.pdf`.
  `scripts/china_harness_tool.py finalize_video_report` exposes this fallback
  to Markdown-first agents. The three Xi videos in
  `gpt-5.4-lock-rerun-{1,2,3}` now all have
  `processor_status=video_render_complete`,
  `content_access.video_body_status=complete_report`, no active locks, and
  final `video_report.pdf`. The summary PDF was regenerated at
  `workspace/xi_jinping_out_before/2027/gpt-5.4-lock-rerun-summary/forecast_report.pdf`
  with video completion `3/3`.
- As of 2026-06-12, the China-specific harness has a strong-report and
  full-trajectory protocol. The user requirement is recorded verbatim in
  `docs/china_harness_strong_report_protocol_zh.md`. `agent_review` now records
  actual materials read, compressed source summaries, visible reasoning memos,
  source-selection notes, and rejected/downweighted materials; these are
  aggregated into `full_trajectory.md`. `task.md` now requires
  `thesis_review.md`, `Mispricing Verdict`, `Paper Trade View`, and
  `Probability Floor Decomposition`.
- Two Taiwan strong-thesis runs were completed on 2026-06-12. Current best run:
  `workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/`.
  It passed `scripts/audit_china_harness_run.py` with score `16`, produced a
  27-page `forecast_report.pdf`, 507-line `full_trajectory.md`, and
  `thesis_review.md`. Final machine-readable forecast: `p_m=0.068`,
  `p_f=0.008`, `p_m_delta=-0.060`, `confidence=0.81`,
  `calibration_status=uncalibrated`, `mispricing_verdict=absolute_overestimate`,
  `paper_trade_view.direction=buy_no`.
- Operational lesson from the Taiwan strong-thesis iteration: the harness can
  now generate the desired strong China-context thesis for this case. The next
  validation should test transfer on Xi 2027, Best Chinese AI Company, or a new
  China event before further Taiwan-specific tuning. Avoid hardcoding the
  Taiwan answer; the result must emerge from source exploration, resolution
  specificity, strategic fit, diplomatic calendar, resource cost, normative fit,
  path dependency, and probability-floor decomposition.
- As of 2026-06-17, T1/T2 text-platform filtering validation for
  `workspace/will_china_invade_taiwan_by/2026/gpt-5.4-t1t2-filter-20260617/`
  passed `scripts/audit_china_harness_run.py` with rubric total `16`. The run
  enforced deep screening on T1 Zhihu/Weibo and T2 Xueqiu: each platform with
  selected URLs completed at least one `process_resource`, and
  `full_trajectory.md` now records candidate pools, selected and rejected
  reasons, engagement and author-quality notes, body-read summaries, and how
  each read changed the next search step. Final machine-readable forecast:
  `p_m=0.068`, `p_f=0.020`, `p_m_delta=-0.048`, `confidence=0.74`,
  `calibration_status=uncalibrated`, `mispricing_verdict=absolute_overestimate`,
  `paper_trade_view.direction=buy_no`. Operational lesson: T1/T2 can produce
  many high-interaction but thin-body, weak-mechanism posts; treat interaction
  as a screening hint only, and expect heavy Xueqiu browser/fallback noise such
  as PDFs, hot pages, old posts, and concept-stock chatter.
- Follow-up lesson from reviewing `gpt-5.4-strong-round2`: video render not
  completing is a harness execution defect. If
  `process_resource` produces a render contract but no `video_report.pdf` and
  no `evidence_card.md`, first run `finalize_video_report`; if it still fails,
  record `video_render_not_completed` in `audit.md` and do not treat the video
  body as read evidence. `search_video_sources` must now
  persist `source_visits/*.md/json` candidate sets before filtering, including
  title, author, URL, views, comments, favorites, likes, publish time, sort
  order, status, and selection/rejection reason. `full_trajectory.md` should use
  `Source：...` first in every Evidence Review, short `./...` run-relative paths,
  and human-readable material titles, not full workspace paths or bare IDs.
- Current uncommitted development includes:
  - `scripts/run_forecast.py`: sports and probability filters.
  - `scripts/run_batch_eval.py`: batch forecasting, stored records, manual
    resolution, BSS reporting, workflow state persistence, `--show-workflow`,
    `--show-market`, `--show-due`, and full condition id display for
    operational commands.
  - `src/beatodds/evaluation/store.py`: DuckDB EvalRecord storage.
  - `src/beatodds/evaluation/workflow_store.py`: stateful workflow DB storage
    for markets, snapshots, parser output, forecast runs, evidence, outcomes,
    market history reads, due-market selection, and evidence query provenance.
  - `tests/test_eval_store.py`: store roundtrip and resolution test.
  - `tests/test_workflow_store.py`: workflow DB roundtrip tests.
  - `docs/current_functionality.md`: current abilities, file map, workflow DB
    schema, local DB counts, and known gaps.
  - `README.md`: local `ref/` submodule setup wording.
  - `ref/`: SSH-backed git submodules for external references; do not vendor
    their full contents into BeatOdds history.
- Local data exists under `data/` and is intentionally ignored.
- `.env` contains local API keys and must never be committed.
- DuckDB file locking matters: do not run two CLI commands that open
  `data/eval.duckdb` in parallel. Run `--show-stored`, `--show-workflow`, and
  batch evaluation commands serially. Run `scripts/run_paper_account.py`
  serially with those commands too.
- Workflow DB writes now normalize timezone-aware datetimes to UTC-naive before
  insertion. Older local rows written before this fix may have local-naive
  timestamps until the market is refreshed by a new forecast run.

## Current Verification Baseline

These commands passed most recently:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -q
node --check gui/web/app.js
uv run python -m py_compile gui/server.py src/beatodds/data/indexers.py src/beatodds/data/storage.py
uv run python -m py_compile src/beatodds/evaluation/paper_store.py scripts/run_paper_account.py
uv run python -m py_compile src/beatodds/evidence/china_query.py src/beatodds/evidence/china_router.py src/beatodds/evidence/china_sources.py src/beatodds/evidence/retriever.py src/beatodds/evaluation/workflow_store.py scripts/run_forecast.py scripts/run_batch_eval.py
uv run python -m py_compile src/beatodds/evaluation/workflow_records.py
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --compute-bss
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market 0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
uv run scripts/backfill_markets.py --incremental
uv run scripts/run_paper_account.py --create-default
uv run scripts/run_paper_account.py --show --transactions
uv run scripts/run_forecast.py --top 1 --dry-run --china-info --exclude-sports --min-prob 0.05 --max-prob 0.95
```

Observed live eval state:

- `data/beatodds.duckdb` contains 40 events and 350 cumulative markets after the
  latest 100-market incremental backfill; 37 events have stored icons and the
  latest 100 markets have stored outcome prices.
- `data/eval.duckdb` contains 14 unresolved compact eval records.
- Stored EvalRecord edge stats: `mean_edge=-0.0188`,
  `mean_abs_edge=0.0249`, `max_edge=+0.0305`, `min_edge=-0.1105`,
  `pct |edge|>3%=35.7%`.
- Workflow tables contain 1 tracked market, 3 snapshots, 2 forecast runs, and 27
  evidence items from live forecast runs.
- The tracked workflow market is
  `0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422`,
  question: `Will JD Vance win the 2028 US Presidential Election?`.
- Its latest stored forecast run has `p_m=0.190`, `p_f=0.150`, `edge=-0.040`,
  `confidence=0.30`, model `deepseek-chat`; the workflow history has 2 forecast
  runs and 27 evidence items.
- `--show-due --stale-hours 24 --top 10` reports `due_count=0`; the only
  tracked workflow market is not yet due under a 24-hour refresh policy.
- `--compute-bss` currently reports no resolved records.
- BSS becomes meaningful only after outcomes are marked or auto-resolved.

## Milestone Plan

### M0: Repository and Environment

Goal: keep the repo reproducible and private.

Done:

- Python project scaffolded.
- `uv sync --extra dev` works.
- GitHub private repo exists.
- `.env` and `data/` are ignored.

Acceptance:

- Fresh checkout can run `uv sync --extra dev`.
- `uv run ruff check .` passes.
- `uv run pytest -q` passes.

Tests:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -q
```

### M1: Live Market Scanner

Goal: use scanner as the live sampling mechanism for forward evaluation.

Current state:

- Gamma and CLOB reads work.
- Scanner filters liquidity, spread, sports, and probability buckets.
- Candidate output includes `MarketMeta`, `PriceSnapshot`, and priority score.
- Batch forecast now persists selected candidates into workflow state.

Next work:

- Move duplicated sports/probability filtering into a shared module.
- Add scanner run persistence to a stateful DB schema.
- Record each scan as a durable sampling decision.

Acceptance:

- A scan creates or updates tracked markets without duplicating rows.
- Each tracked market keeps first seen time, last seen time, latest snapshot, and
  current tracking status.
- Filters are testable without network calls.

Tests:

```bash
uv run pytest -q
uv run scripts/run_scanner.py --top 5
uv run scripts/run_forecast.py --top 1 --dry-run --exclude-sports --min-prob 0.05
```

### M2: Stateful Workflow Database

Goal: replace one-table eval storage with a workflow database that can maintain
long-lived market state.

Planned tables:

- `tracked_markets`: condition id, question, category, slug, event id, deadline,
  active/resolved status, first seen, last seen.
- `market_snapshots`: condition id, token id, bid, ask, midpoint, spread,
  liquidity, snapshot time.
- `resolution_features`: parsed condition type, entities, deadline, queries,
  risk flags, parsed time.
- `forecast_runs`: run id, condition id, snapshot time, evidence cutoff, `p_m`,
  `p_f`, confidence, edge, model, reasoning.
- `evidence_items`: run id, query, title, url, source, summary, published time,
  retrieved time, relevance score.
- `outcomes`: condition id, resolved outcome, resolved time, source.
- `eval_metrics`: metric snapshots after outcomes are known.

Current implementation:

- `workflow_store.py` creates `tracked_markets`, `market_snapshots`,
  `resolution_features`, `forecast_runs`, `workflow_evidence_items`, and
  `outcomes` in `data/eval.duckdb`.
- `run_batch_eval.py` writes full workflow state for each live forecast run.
- `run_batch_eval.py --show-workflow` shows compact workflow counts and tracked
  markets with full condition ids.
- `run_batch_eval.py --show-market <condition_id>` shows one market's tracked
  state, snapshots, resolution features, forecast history, and latest evidence.
- `run_batch_eval.py --show-due --stale-hours N` shows active unresolved markets
  whose latest forecast is missing or stale.
- `EvidenceItem.query` is now populated by `EvidenceRetriever` and preserved in
  `workflow_evidence_items.query`; old rows may still reflect fallback query
  assignment from before this change.
- `workflow_store.load_due_markets()` classifies due markets as
  `never_forecasted` or `stale_<hours>h`.
- A live smoke run on 2026-05-25 wrote 1 forecast run and 16 evidence items.
- `eval_metrics` is not implemented yet.

Acceptance:

- One command can upsert tracked markets and snapshots.
- One command can forecast due markets and persist full evidence and reasoning.
- One command can show current tracked state.
- One command can mark or auto-load outcomes and recompute metrics.

Tests:

```bash
uv run pytest -q
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --compute-bss
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market <condition_id>
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

Add focused DB tests for:

- idempotent market upsert.
- snapshot append behavior.
- forecast run and evidence roundtrip.
- due-market selection.
- resolution update.
- BSS computation after resolution.

Remaining M2 work:

- Add repeated forecast scheduling that refreshes current order book snapshots
  before re-running LLM forecasts.
- Add `eval_metrics` table after baseline grouping is implemented.
- Add migration-safe schema evolution if existing local DuckDB files already
  exist.

### M3: Baselines

Goal: implement report section 4.2 baselines in a comparable way.

Baseline families:

- `market_only`: `p_f = p_m`.
- `search_only_llm`: evidence retrieval plus LLM forecast, no relation miner.
- `market_llm_ensemble`: combine market price and LLM forecast, then calibrate.

Current state:

- `market_only` exists.
- Current live LLM pipeline is close to `search_only_llm`.
- Ensemble and calibration are not implemented.

Acceptance:

- Same tracked market and same snapshot can be evaluated by all baseline
  families.
- Each forecast record stores `signal_type` and `model_version`.
- Metrics can be grouped by baseline family.

Tests:

```bash
uv run pytest -q
uv run scripts/run_batch_eval.py --top 3 --exclude-sports --min-prob 0.05
```

### M4: Evidence Retriever and Forecaster

Goal: make forecasts auditable.

Current state:

- Tavily retrieval works.
- DeepSeek forecast works.
- Batch forecast can persist evidence items used by each forecast run.
- Evidence items now preserve the originating Tavily query in memory and in the
  workflow DB.
- `--show-market` can display latest run evidence and query provenance.

Next work:

- Store raw prompt input or a compact prompt hash plus structured inputs.
- Add scheduled repeated forecasts for the same market over time.

Acceptance:

- Every `p_f` can be traced to the exact evidence items available at prediction
  time.
- Re-running a market later creates a new forecast run, not an overwrite.
- The system can show opinion changes for a market over time.

Tests:

```bash
uv run pytest -q
uv run scripts/run_batch_eval.py --top 1 --exclude-sports --min-prob 0.05
uv run scripts/run_batch_eval.py --show-stored
```

### M5: Resolution and Evaluation

Goal: turn live tracked predictions into scientific evaluation samples.

Current state:

- Manual `--resolve <condition_id> --outcome 1/0` exists.
- Brier score, log loss, and BSS exist.
- No auto-resolution yet.

Next work:

- Add resolver that checks Polymarket/Gamma resolution fields.
- Keep manual override path for ambiguous cases.
- Add calibration error and bucketed metrics by domain, horizon, probability
  bucket, and market age.

Acceptance:

- Resolved outcomes update tracked markets and forecast runs.
- `--compute-bss` reports metrics by baseline family.
- A resolved market keeps enough provenance to audit the outcome.

Tests:

```bash
uv run pytest -q
uv run scripts/run_batch_eval.py --resolve <condition_id> --outcome 1
uv run scripts/run_batch_eval.py --compute-bss
```

### M6: Relation Miner

Goal: keep structure detection useful without letting it dominate the project.

Current state:

- Binary bundle checks exist.
- `neg_risk` checks exist and avoid partial-group false positives by default.

Scope decision:

- Treat Relation Miner as a structural arbitrage detector for now.
- Defer full implication and mutual-exclusion graph until stateful workflow and
  baselines are stable.

Acceptance:

- Relation miner emits explicit structural violations only when all required
  group data is available.
- Structural signals can be stored as a `signal_type` and compared against
  evidence signals.

Tests:

```bash
uv run pytest -q
uv run scripts/run_scanner.py --top 10
uv run scripts/run_scanner.py --top 10 --complete-groups
```

### M7: Calibrator and Ranker

Goal: rank opportunities by estimated net value and later calibrate forecasts.

Current state:

- Ranker computes net edge style scores.
- No learned or empirical calibration yet.

Next work:

- Store enough resolved samples for calibration.
- Start with simple shrinkage: blend `p_m` and `p_f` by confidence and bucket.
- Later add domain/horizon/liquidity calibration.

Acceptance:

- Ranking is reproducible from stored forecast runs.
- Calibration parameters are versioned.
- Metrics can compare uncalibrated and calibrated forecasts.

Tests:

```bash
uv run pytest -q
```

### M8: Paper Trading

Goal: evaluate trading-oriented metrics after predictive evaluation is stable.

Deferred until:

- stateful DB is stable.
- evidence and forecast provenance is persisted.
- enough resolved samples exist to estimate signal quality.

Acceptance:

- Simulated orders account for spread, fees, and available order book depth.
- Reports include PnL, hit rate, drawdown, and performance by bucket.

Tests:

```bash
uv run pytest -q
```

### M9: China-Specific Agentic Harness

Goal: let a main forecasting agent operate through a flexible harness driven by
markdown protocol, workspace files, and tool access primitives.

Current state:

- Phase 1/2 access-tool and workspace foundations are implemented.
- The main harness entry is now Markdown-first and local-agent driven.
- API-agent controller and DeepSeek/OpenAI-compatible loop remain as legacy
  validation paths.
- Earlier DeepSeek + Tavily runs were audited for the Taiwan invasion and Xi
  leadership cases; they are useful fixtures, not the default architecture.
- `AgentRunContext`, `TrajectoryStep`, `AgentToolResult`, and `SourceCard`
  models exist.
- `ChinaForecastWorkspace` creates event / market / agent-run workspaces.
- `ChinaToolRegistry` exposes executable access tools for source registry,
  query generation, Polymarket context, search, render-ready resource
  manifests, and baseline forecast.
- `scripts/run_china_harness.py` creates `task.md`, `tool_manifest.md/json`, and
  `codex_prompt.md` for a local Codex main agent.
- `scripts/china_harness_tool.py` executes one repo tool inside an existing
  workspace and persists artifacts for the local agent to inspect.
- CLI-launched `codex exec --model gpt-5.4` has been validated on the Xi 2027
  market with two independent runs:
  `workspace/xi_jinping_out_before/2027/gpt-5.4-cli-x2c-1` and
  `workspace/xi_jinping_out_before/2027/gpt-5.4-cli-x2c-2`.
- Both CLI runs produced `forecast_report.pdf`, `forecast_report.json`,
  `thesis_review.md`, `audit.md`, and complete video resource artifacts.
- The x2 summary report is in
  `workspace/xi_jinping_out_before/2027/gpt-5.4-cli-x2c-summary/forecast_report.pdf`.
- Both runs passed `scripts/audit_china_harness_run.py` with rubric total `16`;
  both judged `p_f=0.02`, `p_m=0.08`, `p_m_delta=-0.06`,
  `mispricing_verdict=absolute_overestimate`, and paper direction `buy_no`.
- `ChinaForecastWorkspace` now writes `market.md` and `resolution.md` into the
  run directory as well as the market directory, avoiding CLI prompt path
  ambiguity.
- Follow-up from reviewing the same CLI x2 run: both main agents originally left
  softlink-style trajectory appendices in `forecast_report.md`, saying the
  renderer would embed `full_trajectory.md`. This is not acceptable. The final
  report Markdown and PDF must directly contain the substantive
  `full_trajectory.md` body. `scripts/render_forecast_report_pdf.py` now writes
  the embedded trajectory back into `forecast_report.md` before rendering, and
  `scripts/audit_china_harness_run.py` fails runs with
  `softlink_trajectory_appendix` or `missing_full_trajectory_appendix`.
- Final self-review hook for future China harness runs: before stopping, render
  the PDF, run `scripts/audit_china_harness_run.py`, verify
  `has_softlink_trajectory_appendix=False`, verify the appendix contains the
  actual trajectory body rather than "see full_trajectory.md" style text, and
  verify every Evidence Review has `Source：...`, only `./...` run-relative
  metadata paths, and a `./source_visits/...` entry when B站/YouTube candidates
  were searched. Do not expand full candidate tables in report appendices. Then
  run `sync_resource_status --all` to confirm no active video/ASR locks.
- Source quality scoring now writes `raw_metadata.search_quality` into source
  cards and `filtered_quality_count` / `rejected_quality` into search action
  metadata. Latest Xi quality-audit run confirmed context-entity hits and
  category/self-reference filters in artifacts.

Next work:

- Improve official/semi-official recency ranking and domain-specific fetching.
- Improve source access for high-quality Chinese professional media,
  market-professional discussion, and source-specific platform search.
- Run a full forecast where the local main agent can actually start
  `gpt-5.4-mini` video workers from `subagent_spawn_prompt.md`; current CLI
  runs succeed through main-agent/finalizer fallback, but subagent availability
  is not guaranteed in `codex exec` sessions.
- Add real PDF/web document processing outputs.
- Persist agent run metadata into DuckDB for GUI discovery.
- Add GUI display for agent run, trajectory, source cards, claims, and report.
- Add supervisor review / multi-rollout support for high-impact cases.

Acceptance:

- One China-related Polymarket market creates a complete workspace.
- The workspace contains `task.md`, `tool_manifest.md/json`, and `codex_prompt.md`.
- Every search action and source card is replayable from files.
- The local agent can call `scripts/china_harness_tool.py` and append artifacts to
  the same workspace.
- The local agent can run at least three loop steps before report generation.
- At least one post-search decision uses existing source-card evidence to justify
  the next search gap.
- Video resources produce `render_request.json`, `video_report_prompt.md`,
  `subagent_spawn_prompt.md`, and a timeout fallback policy; finished video
  reports add `video_report.pdf` and `evidence_card.md`.
- The final report includes `p_f`, `confidence`, `p_m_delta`, and
  `calibration_status`; the presentation artifact is `forecast_report.pdf`.

Tests:

```bash
uv run ruff check .
uv run pytest -q
uv run scripts/run_china_harness.py --event-title "Will China invade Taiwan by 2026?" --market "Will China invade Taiwan by end of 2026?"
uv run pytest tests/test_china_harness.py tests/test_china_info.py -q
```

## Immediate Next Step

Continue M9 first.

Detailed next plan:

1. Improve official/semi-official recency ranking and domain-specific fetching.
2. Improve high-quality Chinese professional-media and market-professional
   source access.
3. Validate a top-level interactive Codex session that can start
   `gpt-5.4-mini` video subagents, or document the CLI limitation as expected
   capability variance.
4. Add real resource processors for PDF/web/video artifacts.
5. Persist agent run index rows in DuckDB.
6. Surface harness runs in the GUI.

## Planning Protocol

At the start of each substantial turn:

1. Read this file.
2. Run `git status --short --branch`.
3. Identify the active milestone.
4. Create or update a short task plan.

At the end of each substantial turn:

1. Run relevant tests.
2. Update this file if milestone state changed.
3. Summarize what changed, what passed, and what remains blocked.
