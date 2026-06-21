<p align="center">
  <a href="https://github.com/hu-jy23/beatodds"><strong>https://github.com/hu-jy23/beatodds</strong></a>
</p>

# BeatOdds

BeatOdds is a research-grade Polymarket forecasting and paper-trading system.
It scans live prediction markets, retrieves external evidence, runs
forecasting agents, tracks forecast state, and evaluates whether the model
probability `p_f` differs materially from the market probability `p_m`.

```text
edge = p_f - p_m
```

The project was built for an AI-in-Quant research workflow. It includes a
stateful data layer, an event-centric GUI, a Markdown-first agent harness,
China-specific source access tools, and paper-trading infrastructure.

> This repository is for research and engineering evaluation. It is not
> financial advice and does not execute real trades by default.

## Highlights

- **Polymarket data layer**: Gamma API market metadata, CLOB order-book quotes,
  DuckDB storage, and Parquet export.
- **Market scanner**: live candidate discovery with volume, spread, probability,
  and sports filters.
- **Relation miner**: structural checks for binary bundle and neg-risk market
  groups.
- **Evidence forecasting**: resolution parsing, Tavily/news retrieval,
  China-specific routing, LLM forecasting, and forecast provenance.
- **Agentic harness**: local Markdown-first workspaces where agents record
  task files, source visits, trajectory, evidence reviews, audits, and final
  reports.
- **Chinese source access**: support for Bilibili, YouTube, Weibo, Zhihu,
  WeChat, Xueqiu, research reports, newswire sources, and official sources.
- **Video evidence reports**: Bilibili/YouTube resource processing with
  metadata, subtitles/ASR fallback, source cards, and PDF evidence reports.
- **Stateful evaluation**: tracked markets, snapshots, forecast runs,
  evidence items, outcomes, and due-market selection in DuckDB.
- **Paper trading**: local accounts, risk settings, simulated orders, positions,
  NAV evaluation, and GUI account pages.
- **Developer GUI**: event -> market -> YES/NO token side layout, live order
  book, forecasts, evidence, user accounts, and paper-trading panels.

## Repository Layout

```text
beatodds/
  configs/                 China-specific source registry
  docs/                    Documentation, final report, protocol notes
  gui/                     Local web GUI server and frontend
  ref/                     Git submodules for external reference projects
  scripts/                 CLI entry points and report/render utilities
  src/beatodds/
    agents/                Markdown-first agent harness and source tools
    baselines/             Forecast baselines
    calibrator/            Edge ranking and calibration utilities
    common/                Settings, shared schemas, DuckDB schema
    data/                  Gamma/CLOB clients, indexers, storage
    evaluation/            Metrics, workflow DB, paper trading, records
    evidence/              Retrieval, source routing, LLM forecasting
    relation_miner/        Structural market checks
    resolution_parser/     Market/resolution parsing
    scanner/               Live market scanner
  tests/                   Unit tests and harness tests
  workflow_records/        Ignored replay artifact directory scaffold
```

Runtime directories such as `data/`, `workspace/`, `.env`, cookies, and local
agent artifacts are intentionally ignored and are not part of the public
codebase.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- Node.js, only for checking GUI JavaScript syntax
- Optional: a local Codex/agent runtime for full Markdown-first harness runs
- Optional: `ffmpeg`, `yt-dlp`, and Whisper-compatible tooling for video
  resource processing

## Installation

```bash
git clone https://github.com/hu-jy23/beatodds.git
cd beatodds
git submodule update --init --recursive
uv sync --extra dev
```

The `ref/` directory is managed through git submodules. It pins external
reference repositories used during development, including Polymarket SDKs,
paper-trading examples, and prediction-market backtesting references.

## Configuration

Create a local `.env` file:

```bash
cp .env.example .env
```

Common variables:

```text
TAVILY_API_KEY=        # external evidence search
NEWSAPI_KEY=           # optional news provider
DEEPSEEK_API_KEY=      # optional LLM backend
OPENAI_API_KEY=        # optional LLM backend
ANTHROPIC_API_KEY=     # optional LLM backend
DATA_DIR=./data
```

Polymarket private-key variables in `.env.example` are only needed for
trade-capable extensions. The included workflows use read-only data and paper
trading by default.

Never commit `.env`, `data/`, `workspace/`, cookies, local DuckDB files, or
rendered agent workspaces.

## Quick Start

Run tests:

```bash
uv run pytest -q
uvx ruff check .
node --check gui/web/app.js
```

Backfill live market metadata:

```bash
uv run scripts/backfill_markets.py --incremental
```

Scan active markets:

```bash
uv run scripts/run_scanner.py --top 10
uv run scripts/run_scanner.py --top 10 --complete-groups
```

Run a forecast pass:

```bash
uv run scripts/run_forecast.py --top 5 --dry-run
uv run scripts/run_forecast.py --top 5 --china-info --exclude-sports --min-prob 0.05 --max-prob 0.95
```

Inspect stored workflow state:

```bash
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

Start the GUI:

```bash
uv run scripts/run_gui.py --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

## Agent Harness Workflow

The China-specific harness is designed around local workspaces rather than a
fixed remote-agent pipeline. A run starts with a market question and resolution
rule, then creates a workspace with task files and a tool manifest.

Example:

```bash
uv run scripts/run_china_harness.py \
  --event-title "Will China invade Taiwan by 2026" \
  --market "Will China invade Taiwan by end of 2026?" \
  --condition-id "example-condition-id" \
  --event-slug "will_china_invade_taiwan_by" \
  --market-slug "2026" \
  --resolution "Resolve Yes only for a full-scale invasion attempt before 2027-01-01." \
  --p-m 0.068 \
  --agent-name "gpt-5.4" \
  --agent-model "codex:gpt-5.4"
```

Inside the generated workspace, a local agent reads `task.md` and calls:

```bash
uv run scripts/china_harness_tool.py --workspace <run_dir> <tool_name>
```

Important generated artifacts include:

- `task.md`
- `tool_manifest.md`
- `plan.md`
- `source_visits/`
- `agent_reviews/`
- `full_trajectory.md`
- `forecast_report.md`
- `forecast_report.pdf`
- `audit.md`

Use the audit script before treating a run as valid:

```bash
uv run scripts/audit_china_harness_run.py --workspace <run_dir>
```

## Paper Trading

Create and inspect a local paper account:

```bash
uv run scripts/run_paper_account.py --create-default
uv run scripts/run_paper_account.py --show --transactions
```

Run paper-trading evaluation utilities:

```bash
uv run scripts/run_paper_eval.py --help
uv run scripts/run_paper_trader.py --help
uv run scripts/run_paper_maintainer.py --help
```

Paper trading uses local DuckDB state and simulated fills. It is intended for
research evaluation, not production execution.

## Final Report and Submission Files

Final deliverables are under:

```text
docs/final_report/
docs/final_report/submission/
```

The submission directory contains the final PDF, PPTX, and a repository
structure copy for reviewers.

## Development

Recommended checks before committing:

```bash
uvx ruff check .
uv run pytest -q
node --check gui/web/app.js
```

Useful targeted tests:

```bash
uv run pytest tests/test_china_harness.py tests/test_china_info.py -q
uv run pytest tests/test_paper_store.py tests/test_paper_eval.py tests/test_paper_strategy.py -q
uv run pytest tests/test_workflow_store.py tests/test_gamma_client.py -q
```

## Data and Privacy Policy

The public repository contains code, docs, report sources, final report
artifacts, and lightweight configuration files. It excludes:

- local API keys
- cookies and browser sessions
- DuckDB runtime files
- raw downloaded videos
- generated workspaces
- long-running agent artifacts
- temporary source-processing outputs

If you run the harness locally, check `data/`, `workspace/`, and `.env` before
sharing your working tree.

## License

No license has been specified yet. Treat this repository as a course research
artifact unless a license file is added.
