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
- As of 2026-05-31, the LLM forecaster returns a persisted
  `forecast_direction` (`tend_yes`, `tend_no`, or `observe`) in addition to
  `p_f`. The GUI exposes manual `Update topic` and `Update all` controls:
  current-topic updates refresh live market info, retrieve related Tavily news,
  run the LLM forecast when a live CLOB snapshot is available, persist the run
  to the workflow DB, and show related news in the middle rail. The current
  `Update all` endpoint is bounded to a small batch for cost/runtime control.
- GUI middle and right rail panels are now locally reorderable with drag and
  drop and moderately vertically resizable. Panel order is stored in browser
  `localStorage`. The statistics chart grid lives in the right rail with
  per-subplot notes, while the price chart has explicit axes and in-canvas
  legend/label placement to avoid clipping.
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
  batch evaluation commands serially.
- Workflow DB writes now normalize timezone-aware datetimes to UTC-naive before
  insertion. Older local rows written before this fix may have local-naive
  timestamps until the market is refreshed by a new forecast run.

## Current Verification Baseline

These commands passed most recently:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -q
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --compute-bss
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market 0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

Observed live eval state:

- `data/eval.duckdb` contains 6 unresolved records.
- Stored EvalRecord edge stats: `mean_edge=-0.0372`,
  `mean_abs_edge=0.0516`, `max_edge=+0.0305`, `min_edge=-0.1105`,
  `pct |edge|>3%=66.7%`.
- Workflow tables contain 1 tracked market, 1 snapshot, 1 forecast run, and 16
  evidence items from a live JD Vance 2028 run.
- The tracked workflow market is
  `0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422`,
  question: `Will JD Vance win the 2028 US Presidential Election?`.
- Its stored forecast run has `p_m=0.190`, `p_f=0.220`, `edge=+0.030`,
  `confidence=0.40`, model `deepseek-chat`, and 16 evidence items.
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

## Immediate Next Step

Continue M2 first.

Detailed next plan:

1. Add current-price refresh for tracked due markets so repeated forecasts use a
   fresh `p_m` instead of the latest stored snapshot.
2. Add a `--forecast-due` path after price refresh is available.
3. Add idempotent schema evolution for existing local DuckDB files.
4. Add baseline-family grouping so `market_only`, `search_only_llm`, and later
   ensemble forecasts can be compared from the same tables.
5. Run another small live batch after scheduler support exists, then compare
   repeated forecasts for opinion drift.
6. Keep the existing `eval_records` compatibility path until the new workflow DB
   is validated over several live runs.

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
