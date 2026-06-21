# Uploaded Repository Structure

Repository: <https://github.com/hu-jy23/beatodds>

This file records the codebase organization included in the GitHub submission.
Runtime data, local workspaces, cookies, secrets, virtual environments, and
agent run artifacts are intentionally excluded.

## Top-Level Layout

```text
beatodds/
  .env.example
  .gitignore
  .gitmodules
  .python-version
  AGENTS.md
  README.md
  pyproject.toml
  uv.lock
  configs/
  docs/
  gui/
  ref/
  scripts/
  src/
  tests/
  workflow_records/
```

## Source Code

```text
src/beatodds/
  agents/
    access_tools.py              Tool bridge for local agent workspaces
    controller.py                Legacy scripted controller
    llm_agent.py                 LLM-backed legacy agent path
    local_harness.py             Markdown-first harness prompt/protocol builder
    models.py                    Agent context, tool result, source-card models
    platform_source_access.py    Weibo/Zhihu/WeChat/Xueqiu/report/news access
    source_cards.py              Source-card rendering utilities
    source_quality.py            Source quality scoring and filtering
    source_routing.py            Domain/source routing rules
    tool_registry.py             Executable harness tool registry
    video_reporter.py            Deterministic video report fallback/finalizer
    video_source_access.py       Bilibili/YouTube search and candidate handling
    workspace.py                 Event/market/agent workspace layout
  baselines/
    market_only.py
  calibrator/
    ranker.py
  common/
    config.py
    db.py
    types.py
  data/
    clob_client.py
    gamma_client.py
    indexers.py
    storage.py
  evaluation/
    metrics.py
    paper_eval.py
    paper_store.py
    paper_strategy.py
    store.py
    workflow_records.py
    workflow_store.py
  evidence/
    china_query.py
    china_router.py
    china_sources.py
    forecaster.py
    retriever.py
    providers/
      base.py
      mock_provider.py
      tavily_provider.py
  relation_miner/
    miner.py
  resolution_parser/
    parser.py
  scanner/
    scanner.py
```

## Command-Line Entry Points

```text
scripts/
  backfill_markets.py
  run_scanner.py
  run_forecast.py
  run_batch_eval.py
  run_gui.py
  run_china_harness.py
  china_harness_tool.py
  audit_china_harness_run.py
  finalize_video_resource_report.py
  render_forecast_report_pdf.py
  render_research_process_ppt.py
  summarize_china_parallel_runs.py
  run_paper_account.py
  run_paper_eval.py
  run_paper_trader.py
  run_paper_maintainer.py
  run_paper_eval_report.ps1
  run_top_level_china_codex.sh
  top_level_china_main_agent_prompt.md
```

## GUI

```text
gui/
  server.py
  web/
    index.html
    app.js
    styles.css
```

The GUI provides an event-centric Polymarket interface, selected-market order
book, forecast/evidence panels, user accounts, paper-trading controls, and
account analytics.

## Documentation and Final Deliverables

```text
docs/
  current_functionality.md
  gpt_context_report.md
  china_harness_test_plan.md
  china_harness_audit_log.md
  china_harness_last_run_point_check_zh.md
  china_harness_strong_report_protocol_zh.md
  md_first_harness_validation.md
  source_access_implementation_report_zh.md
  source_internal_access_protocol_zh.md
  final_report/
    main.tex
    main.bbl
    references.bib
    neurips_2026.sty
    checklist.tex
    sections/
    figures/
    submission/
      main.pdf
      Proj_Merged(1).pptx
      repository_structure.md
```

## Tests

```text
tests/
  test_china_harness.py
  test_china_info.py
  test_eval_store.py
  test_gamma_client.py
  test_paper_eval.py
  test_paper_store.py
  test_paper_strategy.py
  test_smoke.py
  test_workflow_store.py
```

## Reference Repositories

```text
ref/
  official-polymarket/
    py-clob-client-v2
    agents
  data-backtesting/
    prediction-market-analysis
    prediction-market-backtesting
  strategy-execution/
    polymarket-arbitrage
    polymarket-paper-trader
  agent-benchmark/
    FutureShow
    prediction-market-agent-tooling
```

These are git submodules or pinned reference paths. They support reproducibility
and document the external systems used during implementation.

## Excluded Runtime Paths

```text
.env
data/
workspace/
.venv/
.learnings/
harness_*_variants/
docs/final_report/template_sources/
```

These paths contain local secrets, runtime databases, generated agent artifacts,
or scratch assets and are not part of the uploaded codebase.
