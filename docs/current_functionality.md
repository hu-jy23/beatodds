# BeatOdds 当前功能说明

状态日期：2026-05-25

本文档说明当前仓库已经具备哪些能力、哪些文件支撑这些能力，以及本地
DuckDB 数据库的现况。它用于项目交接和长期开发，不替代面向用户的
README。

## 功能总览

BeatOdds 当前支持一套 Polymarket 错价研究的实时前向评测流程。

核心公式：

```text
edge = p_f - p_m
```

- `p_m`：Polymarket 当前订单簿价格隐含概率。
- `p_f`：BeatOdds 基于证据和 LLM 预测得到的公平概率估计。
- `edge`：正值表示系统认为 YES 被低估；负值表示系统认为 YES 被高估。

当前采用 live collection 的评测策略：在预测时冻结证据，保存预测结果，
等待未来市场结算后再写入 outcome。这样可以避免回测证据泄漏，不依赖完整
历史网页/新闻归档。

## 当前能力

### 1. 读取 Polymarket 元数据和实时价格

能力：

- 从 Polymarket Gamma API 拉取 active/liquid markets。
- 将 Gamma 原始 payload 解析成强类型 `MarketMeta`。
- 从 CLOB 获取实时订单簿。
- 构造包含 bid、ask、midpoint、spread、timestamp 的 `PriceSnapshot`。

支撑文件：

- `src/beatodds/data/gamma_client.py`
  - `GammaClient.get_liquid_markets()` 按 `volume24hr` 拉取活跃市场。
  - `GammaClient.parse_market()` 将 Gamma dict 转为 `MarketMeta`。
  - `GammaClient.get_event_markets()` 拉取完整 event group，用于 neg-risk 检查。
- `src/beatodds/data/clob_client.py`
  - `ClobReadClient.get_order_book()` 封装 Polymarket CLOB v2。
  - `ClobReadClient.get_snapshot()` 将订单簿转为 `PriceSnapshot`。
  - 已处理 CLOB v2 的特殊排序：best bid 和 best ask 都取对应侧最后一个元素。
- `src/beatodds/common/types.py`
  - 定义 `MarketMeta`、`PriceSnapshot`、`PriceHistoryPoint`、`CandidateMarket`。

入口命令：

```bash
uv run scripts/run_scanner.py --top 10
uv run scripts/backfill_markets.py --incremental
```

### 2. 扫描市场并生成候选

能力：

- 拉取高流动性市场。
- 过滤距离关闭太近的市场。
- 对 YES token 取实时盘口快照。
- 按结构价值和可执行性给候选打分。
- 标注 `neg_risk`、`multi_outcome`、`low_volume`、`wide_spread`。

支撑文件：

- `src/beatodds/scanner/scanner.py`
  - `Scanner.scan()` 是主扫描流程。
  - `_compute_flags()` 给市场打标。
  - `_priority()` 偏好 neg-risk、多 outcome、紧 spread、高成交量市场。
- `scripts/run_scanner.py`
  - scanner-only CLI。
- `src/beatodds/common/config.py`
  - 保存 scanner 阈值：最小成交量、最大 spread、最小剩余关闭天数等。

入口命令：

```bash
uv run scripts/run_scanner.py --top 10
uv run scripts/run_forecast.py --top 5 --dry-run
```

当前限制：

- scanner 持久化目前只在 `run_batch_eval.py` 的 forecast 流程里顺带完成。
- 还没有单独的 durable scan-run 表。

### 3. 检测结构性套利

能力：

- 检测二元市场 bundle arbitrage：YES + NO 是否在扣除费用后偏离 1.0。
- 检测 neg-risk group 的价格总和违规。
- 可选补全 neg-risk event group，避免只看到部分成员导致误报。

支撑文件：

- `src/beatodds/relation_miner/miner.py`
  - `RelationMiner.mine()` 生成 relation edges 和 violations。
  - `_check_binary_bundle()` 检查 YES/NO bundle long 和 bundle short。
  - `_check_neg_risk_group()` 检查多市场 neg-risk 总和。
  - `_complete_neg_risk_groups()` 从 Gamma event 数据补齐缺失市场。
- `src/beatodds/common/types.py`
  - 定义 `RelationEdge`、`ConsistencyViolation`、`RelationGraph`。

入口命令：

```bash
uv run scripts/run_scanner.py --top 10
uv run scripts/run_scanner.py --top 10 --complete-groups
```

当前限制：

- 当前 Relation Miner 只覆盖直接结构约束。
- 更一般的 implication/mutual-exclusion graph mining 暂缓。

### 4. 解析市场 resolution 语义

能力：

- 将市场问题和 resolution text 转为结构化特征。
- 抽取 condition type、key entities、deadline、search queries、oracle type、
  ambiguity score、risk flags。
- 通过配置支持 Anthropic、DeepSeek、OpenAI-compatible LLM 后端。

支撑文件：

- `src/beatodds/resolution_parser/parser.py`
  - `ResolutionParser.parse()` 是主 parser。
  - `_call_llm()` 调用配置的 LLM 并要求输出 JSON。
  - 失败时退化为只用市场问题作为搜索 query。
- `src/beatodds/common/types.py`
  - 定义 `ResolutionFeatures`。
- `src/beatodds/common/config.py`
  - 根据环境变量选择 LLM backend。

存储输出：

- `data/eval.duckdb` 里的 `resolution_features` 表。

### 5. 按时间边界检索外部证据

能力：

- 使用 Tavily 搜索外部证据。
- 在任何 search call 之前设置 `evidence_frozen_at`。
- 丢弃发布时间晚于 frozen time 的证据。
- 为每条 evidence 保存其来源 search query。

支撑文件：

- `src/beatodds/evidence/retriever.py`
  - `EvidenceRetriever.retrieve()` 执行 Tavily 查询并返回
    `(evidence_items, evidence_frozen_at)`。
  - 每个 `EvidenceItem` 已带 `query` 字段。
  - 结果按 URL 去重，并按 relevance score 降序排序。
- `src/beatodds/common/types.py`
  - 定义 `EvidenceItem`。
- `src/beatodds/evaluation/workflow_store.py`
  - 将 evidence 持久化进 `workflow_evidence_items`。

存储输出：

- `workflow_evidence_items` 表，字段包括 `run_id`、`condition_id`、`query`、
  `title`、`summary`、`url`、`source`、`published_at`、`retrieved_at`、
  `relevance_score`。

当前限制：

- 本地旧 evidence rows 可能仍使用 fallback query，因为 query provenance 是第一轮 live run 后才补上的。

### 6. 用 LLM 预测公平概率

能力：

- 将市场文本、resolution text、当前 `p_m`、冻结后的 evidence 输入 LLM。
- 输出 JSON forecast：`p_f`、`confidence`、短 reasoning。
- LLM 失败时回退为 `p_f = p_m`。

支撑文件：

- `src/beatodds/evidence/forecaster.py`
  - `LLMForecaster.forecast()` 返回 `ForecastResult`。
  - `_SYSTEM_PROMPT` 要求模型只基于给定 evidence 作判断。
  - DeepSeek 通过 OpenAI-compatible client 和配置的 base URL 调用。
- `src/beatodds/common/types.py`
  - 定义 `ForecastResult`。

存储输出：

- workflow DB 里的 `forecast_runs` 表。
- compact Brier Skill Score 评测用的 `eval_records` 表。

当前限制：

- 还没有保存 prompt hash 或完整 prompt。
- evidence 和 reasoning 已保存，但尚不能完整重构当时的 LLM 输入。

### 7. 机会评分和排序

能力：

- 将 forecast 和 structural violation 转为 edge score。
- 根据 spread 和 fee 估算 net edge。
- 按 value 和 confidence 排序机会。

支撑文件：

- `src/beatodds/calibrator/ranker.py`
  - `Ranker.rank()` 合并结构信号和证据信号。
  - `_net_edge()` 扣除 spread 和配置的 fee。
- `src/beatodds/baselines/market_only.py`
  - `market_only_scores()` 提供 market-only baseline。
- `src/beatodds/common/types.py`
  - 定义 `EdgeScore` 和 `RankedOpportunity`。

当前限制：

- 还没有 empirical calibration。
- 还没有 `market_llm_ensemble`。

### 8. 批量预测并持久化评测记录

能力：

- 扫描实时市场。
- 用 `--exclude-sports` 过滤体育市场。
- 用 `--min-prob` 和 `--max-prob` 过滤市场概率区间。
- 依次执行 parse、retrieve evidence、forecast、persist。
- 同时写 compact eval records 和更完整的 workflow state。

支撑文件：

- `scripts/run_batch_eval.py`
  - 当前 live forward evaluation 的主 CLI。
  - 通过 `evaluation/store.py` 写 compact `EvalRecord`。
  - 通过 `evaluation/workflow_store.py` 写完整 workflow state。
- `src/beatodds/evaluation/store.py`
  - 用于 Brier Skill Score 计算的 compact table。
- `src/beatodds/evaluation/workflow_store.py`
  - stateful workflow DB。

入口命令：

```bash
uv run scripts/run_batch_eval.py --top 5 --exclude-sports --min-prob 0.05
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market <condition_id>
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

### 9. 追踪 outcome 并计算评测指标

能力：

- 手动将市场标记为 YES/NO resolved。
- 同步更新 `eval_records` 和 workflow `outcomes`。
- outcome 已知后计算 Brier Score、market Brier Score、Brier Skill Score、
  log loss、mean edge、mean absolute edge。

支撑文件：

- `scripts/run_batch_eval.py`
  - `--resolve <condition_id> --outcome 1/0`
  - `--compute-bss`
- `src/beatodds/evaluation/store.py`
  - `mark_resolved()` 更新 compact eval rows。
  - `load_eval_records()` 读取 resolved/unresolved records。
- `src/beatodds/evaluation/workflow_store.py`
  - `mark_outcome()` 记录 workflow outcome state。
- `src/beatodds/evaluation/metrics.py`
  - `compute_predictive()` 计算预测指标。
  - `check_temporal_integrity()` 在严格 backtest 场景检查 evidence freeze time
    是否早于 snapshot time。

当前限制：

- 还没有自动 Polymarket/Gamma resolution parser。
- 当前本地 DB 没有 resolved outcomes，因此 BSS 还没有意义。

### 10. 选择需要重新预测的市场

能力：

- 查询 active、unresolved、tracked markets 中 latest forecast 缺失或 stale 的市场。
- due reason 分为 `never_forecasted` 或 `stale_<hours>h`。

支撑文件：

- `src/beatodds/evaluation/workflow_store.py`
  - `load_due_markets(stale_after_hours, limit, now)` 执行 due selection。
- `scripts/run_batch_eval.py`
  - `--show-due --stale-hours N --top K` 打印 due markets。

入口命令：

```bash
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

当前限制：

- 还没有 `--forecast-due`。
- repeated forecasting 前需要先刷新 tracked market 的当前 order-book snapshot，
  否则新 forecast 会使用旧 `p_m`。

## 主要运行流程

### Scanner-only 流程

```text
GammaClient.get_liquid_markets()
  -> GammaClient.parse_market()
  -> ClobReadClient.get_snapshot()
  -> Scanner._compute_flags()
  -> Scanner._priority()
  -> CandidateMarket list
```

主要文件：

- `src/beatodds/data/gamma_client.py`
- `src/beatodds/data/clob_client.py`
- `src/beatodds/scanner/scanner.py`
- `scripts/run_scanner.py`

### Evidence forecast 流程

```text
Scanner.scan()
  -> ResolutionParser.parse()
  -> EvidenceRetriever.retrieve()
  -> LLMForecaster.forecast()
  -> save_forecast_run()
  -> save_eval_records()
```

主要文件：

- `scripts/run_batch_eval.py`
- `src/beatodds/resolution_parser/parser.py`
- `src/beatodds/evidence/retriever.py`
- `src/beatodds/evidence/forecaster.py`
- `src/beatodds/evaluation/workflow_store.py`
- `src/beatodds/evaluation/store.py`

### Evaluation 流程

```text
stored forecast rows
  -> manual or future automatic outcome resolution
  -> compute_predictive()
  -> Brier Skill Score vs market-only baseline
```

主要文件：

- `scripts/run_batch_eval.py`
- `src/beatodds/evaluation/store.py`
- `src/beatodds/evaluation/workflow_store.py`
- `src/beatodds/evaluation/metrics.py`

## 数据库当前状态

数据库路径：

```text
data/eval.duckdb
```

该路径被 git ignore。它是本地运行状态，不应提交。

操作约束：

- DuckDB 使用文件锁。不要同时运行两个会打开 `data/eval.duckdb` 的命令。
- `--show-stored`、`--show-workflow`、`--show-market`、`--show-due` 和 batch
  forecast 命令都应串行运行。

时间约定：

- workflow DB 写入时会把 timezone-aware datetime 统一转成 UTC-naive。
- 旧本地 rows 可能仍是修复前写入的 local-naive 时间，刷新市场后会被新记录覆盖。

### 两层 DB 结构

当前同一个 DuckDB 文件里有两层数据。

`eval_records` 是 compact compatibility layer：

- 用途：每条 forecast sample 一行，供指标计算。
- 使用入口：`--show-stored`、`--resolve`、`--compute-bss`。
- 实现文件：`src/beatodds/evaluation/store.py`。

workflow tables 是 long-running state layer：

- 用途：保留 market discovery、snapshots、resolution parsing、forecast runs、
  evidence、outcomes 的完整生命周期。
- 使用入口：`--show-workflow`、`--show-market`、`--show-due`。
- 实现文件：`src/beatodds/evaluation/workflow_store.py`。

### 当前本地表计数

2026-05-25 从 `data/eval.duckdb` 观测到：

| Table | Rows | 用途 |
|---|---:|---|
| `eval_records` | 6 | 之后计算 BSS 的 compact forecast samples |
| `tracked_markets` | 1 | 长期追踪的市场状态 |
| `market_snapshots` | 1 | 不可变盘口快照 |
| `resolution_features` | 1 | 解析后的 resolution/search features |
| `forecast_runs` | 1 | 完整 forecast run 记录 |
| `workflow_evidence_items` | 16 | forecast run 关联证据 |
| `outcomes` | 0 | 已解决 outcome |

### 当前 compact eval records

`uv run scripts/run_batch_eval.py --show-stored` 当前显示 6 条 unresolved records：

| condition id prefix | `p_m` | `p_f` | edge | model | resolved |
|---|---:|---:|---:|---|---|
| `0x7ad403c3508...` | 0.190 | 0.220 | +0.030 | `deepseek-chat` | `None` |
| `0x3f0743b88e...` | 0.111 | 0.050 | -0.061 | `deepseek-chat` | `None` |
| `0xb4067f8195...` | 0.084 | 0.010 | -0.074 | `deepseek-chat` | `None` |
| `0xd9fb1184af...` | 0.068 | 0.080 | +0.012 | `deepseek-chat` | `None` |
| `0xd39905267d...` | 0.141 | 0.120 | -0.021 | `deepseek-chat` | `None` |
| `0x518a5b030b...` | 0.131 | 0.020 | -0.111 | `deepseek-chat` | `None` |

当前 edge summary：

```text
n = 6
mean_edge = -0.0372
mean_abs_edge = 0.0516
max_edge = +0.0305
min_edge = -0.1105
pct |edge| > 3% = 66.7%
```

当前没有 resolved rows，所以：

```bash
uv run scripts/run_batch_eval.py --compute-bss
```

会输出：

```text
No resolved records yet. Run after markets settle and use --resolve.
```

### 当前 workflow market

`uv run scripts/run_batch_eval.py --show-workflow` 当前显示：

```text
tracked_markets = 1
market_snapshots = 1
forecast_runs = 1
evidence_items = 16
outcomes = 0
```

当前唯一 workflow market：

```text
condition_id = 0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422
question = Will JD Vance win the 2028 US Presidential Election?
status = tracking
resolved = None
```

已存 parser 输出：

```text
condition_type = election
search_queries =
  JD Vance 2028 presidential campaign
  2028 US presidential election polls
  2028 election candidates Republican
  JD Vance VP 2024
```

已存 snapshot：

```text
snapshot_time = 2026-05-25T13:44:01.422388
p_m = 0.190
bid = 0.189
ask = 0.190
spread = 0.001
flags = neg_risk
```

已存 forecast run：

```text
run_id prefix = b60e932d
signal_type = search_only_llm
model = deepseek-chat
p_m = 0.190
p_f = 0.220
edge = +0.030
confidence = 0.40
```

最新 evidence 样例：

| score | query | source/title |
|---:|---|---|
| 1.000 | `JD Vance 2028 presidential campaign` | `ballotpedia.org: J.D. Vance - Ballotpedia` |
| 1.000 | `JD Vance 2028 presidential campaign` | `en.wikipedia.org: 2028 United States presidential election - Wikipedia` |
| 1.000 | `JD Vance 2028 presidential campaign` | `polymarket.com: Presidential Election Winner 2028 Predictions & Odds` |
| 0.999 | `JD Vance 2028 presidential campaign` | `apnews.com: Who is JD Vance, Trump's pick for VP?` |
| 0.999 | `JD Vance 2028 presidential campaign` | `realclearpolling.com: Latest Polls 2028` |

due-market 状态：

```bash
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

当前显示：

```text
due_count = 0
No tracked markets are due for a new forecast.
```

### 工作流表 schema

`tracked_markets`：

- `condition_id`
- `question`
- `description`
- `resolution_text`
- `category`
- `slug`
- `event_id`
- `neg_risk`
- `neg_risk_market_id`
- `token_yes_id`
- `token_no_id`
- `outcome_count`
- `outcomes_json`
- `close_time`
- `created_time`
- `volume_24h`
- `liquidity`
- `active`
- `tracking_status`
- `first_seen_at`
- `last_seen_at`
- `resolved_outcome`
- `resolved_at`
- `resolution_source`

`market_snapshots`：

- `snapshot_id`
- `condition_id`
- `token_id`
- `snapshot_time`
- `midpoint`
- `best_bid`
- `best_ask`
- `spread`
- `volume_24h`
- `source`
- `priority_score`
- `scan_flags_json`

`resolution_features`：

- `condition_id`
- `condition_type`
- `key_entities_json`
- `search_queries_json`
- `has_explicit_deadline`
- `deadline_date`
- `oracle_type`
- `exception_clauses_json`
- `ambiguity_score`
- `risk_flags_json`
- `parsed_at`

`forecast_runs`：

- `run_id`
- `condition_id`
- `snapshot_time`
- `evidence_frozen_at`
- `p_m`
- `p_f`
- `edge`
- `confidence`
- `signal_type`
- `model_version`
- `reasoning`
- `created_at`

`workflow_evidence_items`：

- `evidence_id`
- `run_id`
- `condition_id`
- `query`
- `title`
- `summary`
- `url`
- `source`
- `published_at`
- `retrieved_at`
- `relevance_score`

`outcomes`：

- `condition_id`
- `resolved_outcome`
- `resolved_at`
- `resolution_source`
- `notes`

`eval_records`：

- `condition_id`
- `snapshot_time`
- `evidence_frozen_at`
- `p_m`
- `p_f`
- `signal_type`
- `model_version`
- `resolved_outcome`
- `recorded_at`

### DB 函数

Compact eval store：

- `save_eval_records(records)`
- `load_eval_records(resolved_only=False)`
- `mark_resolved(condition_id, outcome)`
- `edge_distribution_summary(records)`

工作流 store：

- `upsert_tracked_market(market, seen_at=None)`
- `append_market_snapshot(snapshot, priority_score=0.0, scan_flags=None)`
- `save_candidate(candidate, seen_at=None)`
- `save_resolution_features(features)`
- `save_forecast_run(candidate, features, evidence, forecast, evidence_frozen_at)`
- `mark_outcome(condition_id, outcome, resolved_at=None, source='manual')`
- `load_tracked_market(condition_id)`
- `load_tracked_markets(limit=50)`
- `load_market_snapshots(condition_id, limit=20)`
- `load_forecast_runs(condition_id=None, limit=50)`
- `load_due_markets(stale_after_hours=24.0, limit=50, now=None)`
- `load_evidence_for_run(run_id)`
- `workflow_summary()`
- `load_resolution_features(condition_id)`

## 按层划分的文件地图

配置和共享类型：

- `src/beatodds/common/config.py`：环境配置、API keys、模型名称、scanner 阈值、数据路径。
- `src/beatodds/common/types.py`：跨模块共享的 pydantic models。
- `src/beatodds/common/db.py`：较早的通用 DuckDB schema 工具，用于市场数据存储。

数据访问：

- `src/beatodds/data/gamma_client.py`：Gamma API 元数据。
- `src/beatodds/data/clob_client.py`：CLOB 订单簿和价格历史。
- `src/beatodds/data/storage.py`：Parquet 存储工具。
- `src/beatodds/data/indexers.py`：market backfill / incremental indexing。

候选生成：

- `src/beatodds/scanner/scanner.py`：实时市场扫描和优先级评分。
- `scripts/run_scanner.py`：scanner CLI。

结构信号：

- `src/beatodds/relation_miner/miner.py`：bundle 和 neg-risk 检查。

语义/证据信号：

- `src/beatodds/resolution_parser/parser.py`：LLM 解析 resolution text。
- `src/beatodds/evidence/retriever.py`：Tavily evidence retrieval。
- `src/beatodds/evidence/forecaster.py`：LLM probability forecast。

评分：

- `src/beatodds/baselines/market_only.py`：market-only baseline。
- `src/beatodds/calibrator/ranker.py`：net edge 和 opportunity ranking。

评测和状态：

- `src/beatodds/evaluation/store.py`：compact `eval_records` storage。
- `src/beatodds/evaluation/workflow_store.py`：long-running workflow database。
- `src/beatodds/evaluation/metrics.py`：Brier/log-loss metrics。
- `scripts/run_batch_eval.py`：主 live evaluation CLI。

测试：

- `tests/test_smoke.py`：核心 import 和 scoring smoke test。
- `tests/test_eval_store.py`：compact eval store roundtrip 和 resolution。
- `tests/test_workflow_store.py`：workflow DB market/snapshot、forecast/evidence、
  outcome、due-market tests。

参考资料：

- `ref/`：外部参考库的轻量 README pointer tree。应保持小体积，不应放完整上游镜像。

长期上下文：

- `AGENTS.md`：当前 milestone plan、操作约束、已验证命令、下一步工程计划。

## 当前缺口

重要缺口：

- 没有自动 outcome resolver。
- 没有 `eval_metrics` 表。
- 没有现有 DuckDB 文件的 schema migration layer。
- 没有 `--forecast-due` 命令。
- 没有 tracked due markets 的 current-price refresh。
- 没有 prompt hash 或 raw prompt storage。
- 没有 empirical calibration 或 market+LLM ensemble。
- 没有单独的 durable scan-run table。

下一步工程：

1. 为 tracked due markets 刷新当前 order-book snapshots。
2. 在 snapshot refresh 后增加 `--forecast-due`。
3. 增加本地 DuckDB schema migration safety。
4. 在 forecast records 和 metrics 中增加 baseline-family grouping。
