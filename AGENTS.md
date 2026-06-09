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
- As of 2026-06-06, the formal paper trading ledger exists. `paper_store.py`
  now creates `paper_orders`, `paper_fills`, and `paper_positions`, records
  simulated buy fills as cash-debiting `trade` transactions, and aggregates
  positions by account/condition/side. Live paper trading entry point:
  `uv run scripts/run_paper_trader.py --top 5`. It creates/uses the
  `paper-live-1000` account with $1000 starting capital, runs the scanner,
  parser, Tavily retriever, and LLM forecaster, buys YES or NO only when
  executable ask-depth edge passes thresholds, persists workflow/eval records,
  and appends every decision including skips to `data/paper_decisions.jsonl`.
  As of 2026-06-06, paper-trading trial defaults are more exploratory:
  default `--top` is 12, sports are included unless `--exclude-sports` is
  passed, entry thresholds are lower, forecast calls can use more evidence
  items and response tokens, and `--trial-aggressive` expands to at least 20
  markets with smaller tickets to place more diversified paper orders.
  Paper decision evaluation entry point:
  `uv run scripts/run_paper_eval.py --account-id paper-live-1000 --top-k 5`
  or `uv run scripts/run_paper_eval.py --account-id paper-live-1000 --all`.
  It reads `data/paper_decisions.jsonl`, selects buy decisions by forecast
  confidence, resolves token IDs from the log/ledger/local DBs, refreshes CLOB
  best bids, and reports mark-to-market unrealized PnL.
  As of 2026-06-07, `run_paper_eval.py` can also write English timestamped
  Markdown and JSON reports with `--report-dir data/report`; it refreshes live
  CLOB best bid/ask prices on every eval run before computing PnL.
  As of 2026-06-07, `run_paper_eval.py --sell` can close open ledger
  positions after live re-marking. It requires `--account-id` and either
  `--condition-id` for one topic or `--sell-all-eligible`. The default exit
  strategy is profit-taking: sell the selected position when net realizable
  return at the current best bid is at least `--sell-min-return 0.05`.
  Optionally, `--sell-min-score` also sells when
  `(current_bid - avg_entry_price) * confidence` crosses the supplied
  threshold. `--sell-fraction` controls partial exits, `--sell-dry-run` reports
  eligible exits without recording orders, and executed sells credit cash,
  insert sell orders/fills/transactions, and reduce or remove `paper_positions`.
  As of 2026-06-07, `scripts/run_paper_maintainer.py` runs an integrated
  sell-then-buy maintenance pass. It records every considered buy/sell strategy
  decision and money snapshot to ignored `data/paper_strategy_runs.jsonl`, while
  executed buy/sell orders are also appended to `data/paper_decisions.jsonl`.
  The default account is `paper-wise-1000`, created with $1000, risk profile
  `wise`, max order $25, max market exposure $60, max total exposure $600, and
  $250 cash buffer. Wise entry defaults are conservative: scan 300 markets,
  inspect top 6 non-sports targets, max spread 5c, min gross edge 2.5c, min net
  edge 1c, min confidence 0.25, and 0.5%-2.5% cash sizing by edge. Wise exit
  defaults sell at current best bid when return is at least +8%, when
  `(current_bid - avg_entry_price) * confidence >= 0.02`, or when return is
  at or below -20%.
  As of 2026-06-07, the GUI User page has a Maintainer section for the selected
  paper account. It shows the formal ledger positions, earning curve,
  maintainer strategy parameters, recent strategy JSONL decisions, and buttons
  for Manual update, Sell, Purchase, and Maintain. The buttons call
  `/api/maintainer-action`, which runs `run_paper_maintainer.py` for sell-only,
  buy-only, or full sell-then-buy maintenance; the GUI dry-run checkbox defaults
  on so manual testing does not mutate the ledger unless intentionally disabled.
  Maintainer command output is streamed line-by-line into the User page
  console while the subprocess runs, and maintainer buy decisions print compact
  CLI lines for each considered topic. Maintainer sizing now caps new buy
  notional against existing open topic exposure plus fees, so repeated runs
  cannot push one market beyond `max_market_exposure`.
  As of 2026-06-08, `run_paper_maintainer.py` also supports manual exits:
  `--manual-sell --manual-sell-position <condition_id>:YES` for selected
  holdings and `--manual-sell-all` for all open holdings, with `--dry-run` and
  `--sell-fraction` supported. GUI User > Maintainer renders open holdings as
  multi-select cards with Select all, Clear, Sell selected, and Sell all holds;
  these call `/api/maintainer-action` with `action=manual_sell` and respect the
  GUI dry-run checkbox.
  The User > Maintainer earning curve and account money strip now mark current
  open share holds from live CLOB best bids where available. The curve's latest
  PnL is `cash + reserved + current share value - initial cash`; unmarked
  positions fall back to cost basis. Maintainer strategy money snapshots also
  include `open_marked_value`, `open_marked_pnl`, `open_marked_count`, and
  `total_marked_money`.
  The User page `持仓与交易` current-hold groups use real marked hold PnL
  (`current_pnl`) only; they no longer fall back to projected forecast-edge
  money. Rows without a current mark show `--` and an `unmarked` source.
  The User page `持仓与交易` trade-record groups no longer show forecast-time
  `expected edge` as a money result. They match each trade to the current open
  position by `condition_id:side` and display the real current hold PnL when
  marked; closed or unmarked trades show `--`.
  The Maintainer earning curve appends an explicit latest
  `source=live_hold_mark` point using current live-bid share value and open
  hold PnL; older transaction points remain ledger cost-basis snapshots instead
  of being backfilled with today's live mark.
  The money-based earning charts rely on the surrounding panel heading for
  title text and draw explicit min/max y-axis money labels, plus a zero line
  when the range crosses zero, so users can read scale without duplicate chart
  titles. The earning/eval curves also draw point nodes with hover tooltips
  showing PnL at that time, use looser vertical padding, and color positive
  y-axis labels green and negative labels red.
  The standalone User > Maintainer nav entry was merged into User >
  Current shares. The page now renders the earning curve and strategy summary
  above the current-shares ledger, with strategy decisions as a foldable list
  inside that same ledger card. The separate current-hold/manual-sell card and
  separate strategy-decision card are no longer shown in the GUI. The account
  page defaults to English and has a top-bar language toggle for Chinese labels.
  GUI initial `/api/state` intentionally avoids live account position marking
  so page reload is not blocked by many CLOB order-book requests. User-page
  live marks load through `/api/account-context` after first render, and
  maintainer polling uses fast state snapshots with one marked refresh at the
  end.
  `event_detail()` must tolerate selected/stored events whose markets are all
  filtered out of the current event list, such as past events after launch-time
  date filtering. It now builds a minimal event shell instead of indexing into
  an empty derived events list.
  User > Overview now includes Eval Earning Curves for `paper-live-1000` and
  `paper-self-1000`. These read historical JSON reports from
  `data/report_live_1000` and `data/report_self_1000`, prefer top-5 eval
  reports matching the documented commands, and plot report-time PnL histories
  without re-running CLOB evaluation on every GUI load.
  The User account list supports deleting a local paper user. Delete removes
  the account plus formal paper account transactions, orders, fills, and
  positions for that account; if the selected account is deleted, the GUI
  selects another account or recreates the default demo account.
  The User page maintainer console also falls back to recent
  `paper_strategy_runs.jsonl` rows, so CLI-started maintainer runs appear in
  the GUI as terminal-style logs. The Positions/Trades page no longer renders
  missing PnL as `+0.00`; open positions and buy trades show projected edge PnL
  when ledger edge data exists, otherwise they show `--` as unmarked.
  GUI market top-bar update buttons now explicitly run update plus forecast:
  topic, tracked, and all paths call `_refresh_market()` for the chosen markets.
  `_refresh_market()` always attempts an LLM forecast after evidence retrieval;
  it uses live CLOB snapshots when available and falls back to stored Gamma
  outcome prices when live CLOB is unavailable, marking the result with
  `forecast_snapshot_source`.
  Event forecast badges are computed from the latest stored forecast per
  market, not from the global top-edge chart slice, so events with lower-ranked
  forecasts still show the correct forecast count. The left event list and
  selected event badge display the dominant forecast direction (`tend yes`,
  `tend no`, or `observe`) and color the event by that direction.
  `/api/market/<condition_id>` returns both selected market detail and refreshed
  selected event detail, and the frontend merges both so `edgePill` updates
  after live-detail refreshes instead of staying on stale `0 forecasts`.
  Browser console logs are emitted for common controls including event/market
  selection, update+forecast topic/tracked/all, maintainer actions, account
  config/funding/profile actions, tracking, and manual topic actions.
  The User page `持仓与交易` section has a local Refresh positions button that
  reuses the same `/api/state` refresh path as the global Refresh button. When
  the global Refresh button is clicked while User > Positions is active, it
  refreshes account positions/trades and keeps that section selected.
  `持仓与交易` also displays an account money strip with cash, projected share
  hold value, total money (`cash + reserved + shares`), and total earn/loss
  versus the account's initial cash.
  As of 2026-06-08, the left Markets rail search supports adding topics.
  The `Add` button and Enter key call `/api/add-topic`, which searches online
  Polymarket/Gamma data instead of local market records. Exact online lookups
  support condition id, Gamma numeric id, and slug; text queries rank a live
  Gamma market sample. A found online topic is persisted into local DuckDB,
  selected, and tracked; a missing topic shows an inline reminder instead of
  creating a fake topic. The state payload keeps the selected event visible
  even when it falls outside the default top-volume event slice.
  Inspection entry point:
  `uv run scripts/run_paper_account.py --account-id paper-live-1000 --show --orders --positions --transactions`.
- As of 2026-06-07, scanner market universe size is configurable. Gamma
  `/markets` effectively returns about 100 rows per request, so
  `GammaClient.get_liquid_markets()` now paginates with `offset`.
  `Settings.scanner_market_limit` defaults to 500 and can be overridden with
  `SCANNER_MARKET_LIMIT` in `.env`; `scanner_gamma_page_limit` defaults to 100.
  CLI commands using `Scanner` accept `--scan-limit`, e.g.
  `uv run scripts/run_scanner.py --scan-limit 250 --top 5` or
  `uv run scripts/run_paper_trader.py --trial-aggressive --scan-limit 1000`.
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
uv run python -m py_compile scripts/run_paper_trader.py
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --compute-bss
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market 0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
uv run scripts/backfill_markets.py --incremental
uv run scripts/run_paper_account.py --create-default
uv run scripts/run_paper_account.py --show --transactions
uv run scripts/run_paper_trader.py --top 5
uv run scripts/run_paper_trader.py --trial-aggressive
uv run scripts/run_paper_eval.py --account-id paper-live-1000 --top-k 5
uv run scripts/run_paper_eval.py --account-id paper-live-1000 --all
uv run scripts/run_paper_eval.py --account-id paper-live-1000 --all --report-dir data/report
uv run scripts/run_paper_eval.py --account-id paper-live-1000 --all --condition-id <condition_id> --sell --sell-dry-run
uv run scripts/run_paper_maintainer.py --account-id paper-wise-1000 --init-only
uv run scripts/run_paper_maintainer.py --account-id paper-wise-1000
uv run scripts/run_scanner.py --scan-limit 250 --top 5
uv run python -m py_compile gui/server.py
node --check gui/web/app.js
uv run python -m py_compile gui/server.py src/beatodds/data/gamma_client.py
uv run ruff check gui/server.py src/beatodds/data/gamma_client.py
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
- Paper-trading PnL is now evaluated through the durable ledger plus live
  re-marking/re-checking. Decision logs live under ignored `data/` and should
  not be committed.
- Scheduled paper eval reports should use `--report-dir data/report` and must
  refresh current CLOB prices on each run before computing PnL.
- The scheduled paper eval automation should not run `uv sync` or rebuild
  `.venv`; PyPI access may fail in automation and the current `odds` Conda
  interpreter is Python 3.12 while the preserved project environment is Python
  3.11. It also cannot rely on `.venv` pointing to
  `C:\Users\Ender\AppData\Roaming\uv\python\...`, because Codex automation may
  be blocked from launching that AppData uv-managed base interpreter. Keep the
  workspace-local copied runtime under ignored `.runtime/` and run the committed
  wrapper instead:
  `powershell -NoProfile -ExecutionPolicy Bypass -File
  scripts\run_paper_eval_report.ps1`. The wrapper rewrites `.venv\pyvenv.cfg`
  to the workspace-local CPython 3.11 runtime, verifies `pydantic`, and then
  runs the report command.
- Latest aggressive trial on 2026-06-06 forecasted 20 markets, placed 16 paper
  buys, and left `paper-live-1000` with $502.04 cash, 18 total orders, and 17
  open positions.
- Latest paper eval on 2026-06-06: top-5 confidence buys marked 5/5,
  invested $187.19, current bid liquidation value $185.07, PnL -$2.12
  (-1.13%). All buys marked 18/18, invested $497.96, value $488.31,
  PnL -$9.65 (-1.94%). This is mark-to-market at current best bids, not final
  resolution PnL.
- Scanner pagination smoke on 2026-06-07:
  `uv run scripts/run_scanner.py --scan-limit 250 --top 5` fetched 250 Gamma
  markets and produced 179 CLOB-backed candidates.
- GUI add-topic browser smoke on 2026-06-08: searching
  `Iran closes its airspace` and clicking Add selected/tracked a matching
  market; searching `zzzz no such beatodds topic 987654321` showed the inline
  not-found reminder.
- GUI online add-topic smoke on 2026-06-08: `/api/add-topic` and the browser UI
  search Polymarket/Gamma online, added `Will Bitcoin reach $100,000 in June?`
  from the live API, and showed `No online Polymarket topic matched...` for a
  junk query.
- GUI token button price smoke on 2026-06-08: selected-event market cards now
  refresh YES/NO token buttons from live CLOB best asks during
  `/api/market/<condition_id>` refresh, falling back to stored Gamma outcome
  prices when live books are unavailable. Browser smoke showed live ask prices
  on the selected Bitcoin market token buttons.
- GUI topic list fix on 2026-06-08: `GuiData.markets()` no longer filters all
  markets to the single global max `fetched_at` batch. Online add-topic writes
  can create one-row newer batches, so the left topic list must read the full
  local market universe and only apply `event_id` filtering for event detail.
  Smoke check returned 103 visible events after the fix.
- GUI fresh-topic ingestion on 2026-06-08: launch/state reads now filter out
  markets/events with stored end dates earlier than the current date, while
  keeping unknown end-date rows. The left rail has a `Get new topics` button
  with editable cap defaulting to 100. It calls `/api/get-new-topics`, fetches
  live Gamma market pages from a persisted `topic_feed_offset`, skips already
  stored condition ids, writes metadata only, and does not run forecasts or
  Tavily/LLM calls. Repeated clicks advance the offset and add the next unseen
  current topics. Browser smoke with cap 2 increased visible events from 80 to
  82 and showed `Added 2 fresh online topics.`

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

Current state:

- Account, order, fill, and position ledger tables exist in `data/eval.duckdb`.
- `run_paper_trader.py` performs a live buy-only paper pass from $1000 capital,
  logs all decisions to JSONL, and persists workflow/eval provenance.
- `run_paper_eval.py` can re-check `paper_decisions.jsonl`, select top-k
  confidence buys or all buys, and report current bid-based unrealized PnL.
- `run_paper_eval.py --sell` can close eligible open positions at current best
  bid using a configurable profit-taking or confidence-weighted score trigger.
- `run_paper_maintainer.py` can maintain a wise paper account by selling
  eligible open positions, buying new high-threshold opportunities, and logging
  strategy params plus money snapshots to `data/paper_strategy_runs.jsonl`.
- GUI User > Maintainer visualizes the formal paper ledger, earning curve,
  strategy params, and recent maintainer JSONL decisions, with manual update,
  sell-only, buy-only, and full maintain controls.
- Durable mark snapshots, final resolution PnL, and drawdown/hit-rate reporting
  still need to be added.

Acceptance:

- Simulated orders account for spread, fees, and available order book depth.
- Reports include PnL, hit rate, drawdown, and performance by bucket.

Tests:

```bash
uv run pytest -q
```

## Immediate Next Step

Continue M2/M8 together: keep scheduler and workflow provenance work moving,
and add paper-trading re-check/mark-to-market reporting after the first live
paper run creates positions.

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
7. Extend `run_paper_eval.py` to append durable mark snapshots to the paper
   ledger/log and add final resolution PnL plus drawdown/hit-rate reports.

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
