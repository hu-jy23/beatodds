# BeatOdds 当前功能说明

状态日期：2026-06-10

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

- 从 Polymarket Gamma API 拉取 active/liquid markets 及其关联 events。
- 将 Gamma 原始 payload 解析成强类型 `EventMeta` 和 `MarketMeta`。
- 保存 event icon/image，以及 market outcomes/outcome prices，用于 GUI 展示
  YES/NO token 价格。
- 从 CLOB 获取实时订单簿。
- 构造包含 bid、ask、midpoint、spread、timestamp 的 `PriceSnapshot`。

支撑文件：

- `src/beatodds/data/gamma_client.py`
  - `GammaClient.get_liquid_markets()` 按 `volume24hr` 拉取活跃市场。
  - `GammaClient.get_event()` 拉取单个 event 的完整元数据。
  - `GammaClient.parse_event()` 将 Gamma event dict 转为 `EventMeta`。
  - `GammaClient.parse_market()` 将 Gamma dict 转为 `MarketMeta`。
  - `GammaClient.get_event_markets()` 拉取完整 event group，用于 neg-risk 检查。
- `src/beatodds/data/clob_client.py`
  - `ClobReadClient.get_order_book()` 封装 Polymarket CLOB v2。
  - `ClobReadClient.get_snapshot()` 将订单簿转为 `PriceSnapshot`。
  - 已处理 CLOB v2 的特殊排序：best bid 和 best ask 都取对应侧最后一个元素。
- `src/beatodds/common/types.py`
  - 定义 `EventMeta`、`MarketMeta`、`PriceSnapshot`、`PriceHistoryPoint`、
    `CandidateMarket`。

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
- 增加中国相关字段：`event_type`、`china_relevance`、`geography`、
  `resolution_source_hint`、`source_routing_hints`。
- parser 失败时也会用 deterministic heuristic 标出 China relevance 和 event type。
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

- 使用 provider-based retriever 搜索外部证据；默认 provider 是 Tavily。
- 在任何 search call 之前设置 `evidence_frozen_at`。
- 丢弃发布时间晚于 frozen time 的证据。
- 为每条 evidence 保存其来源 search query、provider、source type、可靠性先验、
  resolution relevance 和 raw metadata。
- 支持 `--china-info`：对中国相关市场追加中文 query expansion、官方 query 和
  site/domain query；非中国相关市场不会触发 China routing。

支撑文件：

- `src/beatodds/evidence/retriever.py`
  - `EvidenceRetriever.retrieve()` 编排 provider 查询并返回
    `(evidence_items, evidence_frozen_at)`。
  - 每个 `EvidenceItem` 已带 `query`、`provider`、`source_type`、
    `resolution_relevance` 等字段。
  - 结果按 URL 去重，并按 relevance score 降序排序。
- `src/beatodds/evidence/providers/base.py`
  - 定义 `SearchQuery`、`SearchResult`、`SearchProvider`。
- `src/beatodds/evidence/providers/tavily_provider.py`
  - Tavily baseline provider。
- `src/beatodds/evidence/providers/mock_provider.py`
  - 单元测试和 fixture development 用 mock provider。
- `src/beatodds/evidence/china_query.py`
  - deterministic 中文 query expansion。
- `src/beatodds/evidence/china_sources.py`
  - 读取 `configs/china_sources.json`。
- `src/beatodds/evidence/china_router.py`
  - 根据 `event_type` 和 source hints 选择官方、监管、交易所、公司公告等来源。
- `src/beatodds/common/types.py`
  - 定义 `EvidenceItem`。
- `src/beatodds/evaluation/workflow_store.py`
  - 将 evidence 持久化进 `workflow_evidence_items`。

存储输出：

- `workflow_evidence_items` 表，字段包括 `run_id`、`condition_id`、`query`、
  `title`、`summary`、`url`、`source`、`published_at`、`retrieved_at`、
  `relevance_score`、`provider`、`source_type`、`direction`、`strength`、
  `resolution_relevance`、`reliability_prior`、`dedupe_key`、`raw_metadata_json`。

当前限制：

- 本地旧 evidence rows 可能仍使用 fallback query，因为 query provenance 是第一轮 live run 后才补上的。

### 6. China-specific Local Agent Harness 基建

能力：

- 为每个 `event / market / agent_run` 创建独立 workspace。
- 默认入口是 Markdown-first：`scripts/run_china_harness.py` 只创建 workspace、
  `task.md`、`tool_manifest.md/json` 和 `codex_prompt.md`，不再启动 API 远程
  agent 作为主循环。
- 主 agent 预期是本地 Codex / `gpt-5.4-mini`，读取 `task.md` 后自己维护
  `plan.md`、`trajectory.md`、`claims.md`、`audit.md` 和最终 report。
- 将 agent loop 的 trajectory 写成 `trajectory.md` 和 `trajectory.jsonl`。
- 将 search action 写入 `search_actions/`。
- 将视频/社媒平台搜索的筛选前候选集写入 `source_visits/`，包括标题、
  作者、URL、播放量、评论数、收藏数、点赞数、排序来源、状态和筛选理由。
- 将有用 source 写成 `sources/{source_category}/source_card.md/json`。
- 提供 `scripts/china_harness_tool.py`，本地 agent 可用命令调用 repo tools，
  每次调用都会自动落盘 search action、source card、claim 和 trajectory step。
- 提供 `search_web` access tool，可接 Tavily 或 MockSearchProvider。
  - 默认过滤 Polymarket/PolyPredict 这类预测市场自引用页面，避免把 `p_m`
    来源当作事实 evidence。
  - 对 `foreign_crosscheck` 等 source category 过滤 YouTube/Facebook 等
    social/video 结果；这些结果应通过 `expert_social` 显式处理。
  - 对 search results 做 source quality 打分、过滤和重排；保留结果会在
    `raw_metadata.search_quality` 记录 score/reasons，被过滤结果会进入
    search action metadata 的 `rejected_quality`。
  - quality scoring 会结合 query 和 market context 核心实体，减少宽泛官方
    query 返回历史人物、站点目录、导航页的概率。
- 提供 `export_source_registry`，把中国 source registry 写入 workspace。
- 提供 `generate_china_queries`，生成候选中文、英文、site query。
  - 已修复 Xi leadership case 中因为 `Central Military Commission` 误触发台海
    query template 的问题。
- 提供 `read_polymarket_context`，读取 run context 和本地 Polymarket DB 信息。
- 提供 `model_baseline_forecast`。
  - 默认测试/mock 路径使用 market-anchored fallback。
  - 只有显式使用 `scripts/china_harness_tool.py --enable-llm-baseline` 时才允许
    DeepSeek/OpenAI-compatible independent baseline。
  - baseline confidence 上限为 0.75，`calibration_status` 固定为
    `uncalibrated`，直到引入经验校准。
- 提供 `process_resource`。
  - LLM tool call 支持通过 `tool_call.url` 或 URL 型 `tool_call.query` 传入资源 URL。
  - 对 YouTube / Bilibili URL 会检查 metadata、评论、字幕/正文可访问状态。
  - 会在 `artifacts/resources/<resource>/` 写入 `resource_processor.json`、
    `source_card.md`、`render_request.json`、`video_report_prompt.md`、
    `subagent_spawn_prompt.md` 和 `artifact_index.md`。
  - `render_request.json` 指定 `youtube-render-pdf` 或 `bilibili-render-pdf`、
    本地 `SKILL.md` 路径、`gpt-5.4-mini` worker、`multi_agent_v1.spawn_agent`
    参数、orientation、output_dir、timeout、expected outputs 和 fallback policy。
  - `subagent_spawn_prompt.md` 是给 `gpt-5.4` 主 agent 的启动说明：把对应
    render skill 作为 `items[type=skill]` 交给 `gpt-5.4-mini`，worker 只写
    该资源 output_dir。
  - 若 `video_report.pdf` / `evidence_card.md` 未在 timeout 内生成，主 agent
    应把该视频写入 coverage gap 或 low-signal source，然后继续 synthesis。
- 提供强报告和全轨迹协议。
  - 用户原始要求和工程协议记录在
    `docs/china_harness_strong_report_protocol_zh.md`。
  - `task.md` 要求主 agent 生成 `thesis_review.md`、`full_trajectory.md`、
    `Mispricing Verdict`、`Paper Trade View` 和
    `Probability Floor Decomposition`。
  - `scripts/china_harness_tool.py agent_review` 可保存实际阅读材料、材料摘要、
    可展示推理札记、source 选择说明、拒绝或降权材料、信息缺口和下一步搜索决策。
  - `src/beatodds/agents/workspace.py` 会把这些 review 汇总进
    `full_trajectory.md`，最终报告可作为附录引用。
  - `full_trajectory.md` 每个 Evidence Review 先写 `Source：...`，再写
    `./...` run 内相对短路径；视频候选池只保留 `./source_visits/...`
    入口，完整标题、互动指标、选择/拒绝状态和理由留在候选池原文，避免报告附录过长。
  - `scripts/audit_china_harness_run.py` 会检查 `full_trajectory.md`、
    `thesis_review.md`、强结论 section、证据路径数量和 agentic trajectory。
  - 当前通过验收的 Taiwan strong-thesis run 在
    `workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/`：
    `p_m=0.068`，`p_f=0.008`，`mispricing_verdict=absolute_overestimate`，
    `paper_trade_view.direction=buy_no`。
- 提供 `scripts/render_forecast_report_pdf.py`，把 `forecast_report.md/json` 渲染成
  `forecast_report.pdf`，并在 `artifacts/report_charts/` 下输出概率/置信度图表。
  - 对多 outcome 市场，支持从 `metadata.company_probabilities`、
    `metadata.outcome_probabilities` 或顶层 `outcomes` 读取 outcome 分布并画图。
- `ChinaAgentLoopController`、`ScriptedChinaAgent` 和 `LLMChinaAgent` 保留为 legacy
  validation/API-agent 路径，不再是默认主 harness。

支撑文件：

- `src/beatodds/agents/models.py`
  - 定义 `AgentRunContext`、`TrajectoryStep`、`AgentToolResult`、`SourceCard`。
  - 定义 `ForecastOutcomeProbability` 和 `MultiOutcomeForecast`，用于多 outcome
    市场的机器可读概率分布。
- `src/beatodds/agents/workspace.py`
  - `ChinaForecastWorkspace` 创建和维护文件 workspace。
- `src/beatodds/agents/source_cards.py`
  - 将搜索结果渲染成 source card。
- `src/beatodds/agents/source_quality.py`
  - 对搜索结果做质量评分、上下文实体检查、boilerplate 检测和重排。
- `src/beatodds/agents/tool_registry.py`
  - `ChinaToolRegistry` 管理 access tools。
  - `SearchTool` 包装 provider-neutral search。
- `src/beatodds/agents/local_harness.py`
  - 渲染 `task.md`、`tool_manifest.md/json` 和 `codex_prompt.md`。
- `src/beatodds/agents/access_tools.py`
  - 实现 source registry、query generation、Polymarket context、baseline 和 resource stubs。
- `src/beatodds/agents/controller.py`
  - legacy scripted/API agent loop controller。
- `src/beatodds/agents/llm_agent.py`
  - legacy DeepSeek/OpenAI-compatible API agent。
- `scripts/run_china_harness.py`
  - 从 Q + resolution bootstrap md-first local agent workspace。
- `scripts/china_harness_tool.py`
  - 在既有 workspace 中执行单个 repo tool 并自动落盘。
- `scripts/render_forecast_report_pdf.py`
  - 生成最终 PDF 报告和简单图表。

默认 workspace：

```text
workspace/{event_slug}/{market_slug}/{agent_name}/
```

入口命令：

```bash
uv run scripts/run_china_harness.py \
  --event-title "Will China invade Taiwan by 2026?" \
  --market "Will China invade Taiwan by end of 2026?" \
  --condition-id 0xtaiwan \
  --p-m 0.0625

# 默认 agent workspace 目录是 gpt-5.4-mini。
# 如果需要重复实验，应显式传入 --agent-run-id。

uv run scripts/china_harness_tool.py --workspace "<run_dir>" read_polymarket_context
uv run scripts/china_harness_tool.py --workspace "<run_dir>" export_source_registry
uv run scripts/china_harness_tool.py --workspace "<run_dir>" search_web \
  --query "Taiwan invasion 2026 official assessment" \
  --source-category foreign_crosscheck \
  --max-results 5

uv run scripts/render_forecast_report_pdf.py --workspace "<run_dir>"
```

当前限制：

- 真实 DeepSeek + Tavily 的旧 API-agent 路径已跑通过 Taiwan invasion 和 Xi
  leadership 两类 case，但该路径现降级为 legacy validation。
- 新 local-agent 路径已经能生成 `task.md` / `tool_manifest.md`，并能通过
  `china_harness_tool.py` 把 search action、source card、claim、trajectory 追加到
  同一 workspace。
- 最终展示报告现在要求生成 `forecast_report.pdf`；PDF renderer 会读取
  `forecast_report.md/json` 并 include 概率/置信度图表。
- agentic search 审计日志在 `docs/china_harness_audit_log.md`。
- stop/budget policy 还很简单。
- `model_baseline_forecast` 已能走独立 LLM baseline，但默认 local-agent run 不启用。
- DB 只应记录 workspace path / report path，当前还未对接。
- Video resource processor 当前生成 subagent-ready manifest/prompt；真实视频正文
  PDF 由 `gpt-5.4-mini` worker 使用 `bilibili-render-pdf` 或
  `youtube-render-pdf` skill 产出。
- source quality filter 已能过滤一部分低质量结果，但官方和半官方 search 仍可能
  混入旧材料；后续需要 domain-specific fetch 和更好的 recency/rerank。

### 7. 用 LLM 预测公平概率

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
- `workflow_records/` 会保存 market、snapshot、parser features、evidence 和
  forecast reasoning 的文件副本，可重建核心 workflow 输入。
- 完整 LLM prompt hash 和 provider response 原文仍未保存。

### 8. 机会评分和排序

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

### 9. 批量预测并持久化评测记录

能力：

- 扫描实时市场。
- 用 `--exclude-sports` 过滤体育市场。
- 用 `--min-prob` 和 `--max-prob` 过滤市场概率区间。
- 依次执行 parse、retrieve evidence、forecast、persist。
- 同时写 compact eval records 和更完整的 workflow state。
- 每次 `save_forecast_run()` 都会额外写一份本地 workflow replay artifact 到
  `workflow_records/`。

支撑文件：

- `scripts/run_batch_eval.py`
  - 当前 live forward evaluation 的主 CLI。
  - 通过 `evaluation/store.py` 写 compact `EvalRecord`。
  - 通过 `evaluation/workflow_store.py` 写完整 workflow state。
- `src/beatodds/evaluation/store.py`
  - 用于 Brier Skill Score 计算的 compact table。
- `src/beatodds/evaluation/workflow_store.py`
  - stateful workflow DB。
- `src/beatodds/evaluation/workflow_records.py`
  - 将每次 forecast run 复制成 JSON 和 Markdown artifact。
- `workflow_records/`
  - 本地 workflow replay 文件夹，目录说明进仓库，运行记录默认被忽略。

入口命令：

```bash
uv run scripts/run_batch_eval.py --top 5 --exclude-sports --min-prob 0.05
uv run scripts/run_batch_eval.py --top 5 --china-info --exclude-sports --min-prob 0.05
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market <condition_id>
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
```

### 10. 追踪 outcome 并计算评测指标

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

### 11. 选择需要重新预测的市场

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

### 12. Paper trading account 基建

能力：

- 在 `data/eval.duckdb` 中维护 paper trading account。
- 保存账户初始资金、现金余额、reserved cash、账户状态、风险参数和 agent
  trading pattern。
- 用 append-only `paper_account_transactions` 记录所有现金变化。
- 支持创建默认 demo account、创建自定义 account、充值、提现、reserve cash、
  release reserved cash、更新风险参数和交易 sizing 配置。
- 默认账户配置是 all-in per trade、0 fee、0 slippage；GUI 可以切换 fixed /
  fraction sizing。
- 为后续 `paper_orders`、`paper_fills`、`paper_positions`、`paper_marks` 提供
  `account_id` 和资金约束基础。

支撑文件：

- `src/beatodds/evaluation/paper_store.py`
  - `create_paper_account()` 创建账户并写 initial capital transaction。
  - `ensure_default_paper_account()` 创建或读取默认 `demo` 账户。
  - `deposit_cash()`、`withdraw_cash()`、`reserve_cash()`、
    `release_reserved_cash()` 写现金流水并更新账户余额。
  - `update_risk_params()` 更新 sizing mode、order fraction、auto-trade 开关、
    max order/market/event/category/total exposure、cash buffer、fee/slippage 参数。
  - `load_paper_account()`、`load_paper_accounts()`、
    `load_account_transactions()`、`account_summary()` 查询账户状态。
- `scripts/run_paper_account.py`
  - 本地账户管理 CLI。
- `src/beatodds/common/types.py`
  - 定义 `PaperAccount` 和 `PaperAccountTransaction`。
- `tests/test_paper_store.py`
  - 覆盖 account create/load/update、现金流水、reserve/release、负余额保护。

入口命令：

```bash
uv run scripts/run_paper_account.py --create-default
uv run scripts/run_paper_account.py --show
uv run scripts/run_paper_account.py --transactions
uv run scripts/run_paper_account.py --deposit 100 --memo "manual top up"
uv run scripts/run_paper_account.py --reserve 25 --memo "paper order reserve"
uv run scripts/run_paper_account.py --release 25 --memo "paper order cancelled"
uv run scripts/run_paper_account.py --max-order-notional 50 --max-event-exposure 500
```

当前限制：

- 尚未实现 `paper_orders`、`paper_fills`、`paper_positions`、`paper_marks`、
  `paper_settlements`。
- 账户现金 reserve 还没有绑定真实 paper order id。
- GUI 的 Login / Trading Config 已接入 `paper_accounts`。当前 paper deal 仍是
  simulated action log，尚未写入正式 order/fill/position 账本。

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

### 本地 GUI 流程

```text
data/beatodds.duckdb
  -> events + latest markets
  -> left rail event list
  -> selected event detail
  -> selected market workbench
```

当前 GUI 已按 Polymarket 的 event → market 层级组织：

- 左侧是 event list，不再是 market list。
- 中部展示 selected event 的 icon、tags、title/meta、event 下 markets、规则/盘口
  背景 tab、event 级统计和 brief。
- event 下每个 market 以卡片展示，带显眼的 YES/NO token display button。
- 点击 YES 或 NO 会切换当前查看的 token side，并用对应 token id 读取 CLOB
  order book。
- 右侧展示 selected market 的 live price、完整可滚动盘口、forecast/evidence 和
  operator actions。盘口显示 asks/bids 的 price、shares、total，不画累积深度曲线。
- 顶栏提供当前 paper user 入口。点击当前 user 进入 User page；登录不做密码，
  在 User page 内列出本地已注册 paper accounts，点击账户按钮即可切换；
  也可以只输入 name 创建新本地账户。
- GUI 已拆出独立 User page。顶栏点击当前 user 进入账户页，主市场页不再堆放
  账户配置。
- User page 左侧 sidebar 提供 Overview、基础设置、资金管理、持仓与交易、
  Agent Pattern 入口。
- User page 的基础设置支持 name、icon URL、notes；资金管理支持 deposit /
  withdraw 并写入 `paper_account_transactions`。
- User page 的持仓与交易页展示当前 simulated exposure、历史 GUI paper deal
  记录、NAV 曲线、账户统计量。正式 orders/fills/positions 尚未实现，所以这里
  目前聚合的是 GUI simulated paper deal。
- User page 的 Agent Pattern 是 agent trading pattern 驾驶室，可以配置 all-in/
  fixed/fraction sizing、order fraction、max order、cash buffer、fee、slippage、
  total exposure 和 autonomous paper order 开关。
- `Paper deal` 会读取当前登录账户的 sizing/fee/slippage 配置来估算 notional、
  shares、fee 和 projected PnL。
- `/api/state` 只返回 event shell 和非阻塞状态。
- `/api/market/<condition_id>?side=YES|NO` 单独加载 live CLOB quote，避免首屏被慢
  盘口请求卡住，并把 forecast/chart 概率转换到当前 token side。
- 顶部提供 Dark/Light toggle；dark mode 使用接近 Polymarket 的黑色风格。
- GUI runtime 状态仍保存在 ignored 的 `data/gui_state.json`。

主要文件：

- `gui/server.py`
- `gui/web/index.html`
- `gui/web/app.js`
- `gui/web/styles.css`
- `scripts/run_gui.py`

## 数据库当前状态

数据库路径：

```text
data/beatodds.duckdb
data/eval.duckdb
```

这些路径被 git ignore。它们是本地运行状态，不应提交。

操作约束：

- DuckDB 使用文件锁。不要同时运行两个会打开 `data/eval.duckdb` 的命令。
- `--show-stored`、`--show-workflow`、`--show-market`、`--show-due` 和 batch
  forecast 命令都应串行运行。
- `scripts/run_paper_account.py` 也会打开 `data/eval.duckdb`，应与 batch eval /
  workflow 查询命令串行运行。

时间约定：

- workflow DB 写入时会把 timezone-aware datetime 统一转成 UTC-naive。
- 旧本地 rows 可能仍是修复前写入的 local-naive 时间，刷新市场后会被新记录覆盖。

### 两类 DB 结构

`data/beatodds.duckdb` 是市场大盘层：

- `events`：Gamma event 元数据，作为用户可理解的组织容器。
- `events.image`、`events.icon`：event 页视觉标识。
- `markets`：最小可交易单元，带 `condition_id` 和 `event_id`。
- `markets.outcome_prices_json`：YES/NO 等 outcome 的当前 Gamma price，用于 GUI
  token display。
- `price_snapshots`、`price_history`、`resolutions`、`evidence_items`、
  `edge_scores`：较早的通用市场数据表。

`data/eval.duckdb` 是 live workflow/evaluation 层。

`eval_records` 是 compact compatibility layer：

- 用途：每条 forecast sample 一行，供指标计算。
- 使用入口：`--show-stored`、`--resolve`、`--compute-bss`。
- 实现文件：`src/beatodds/evaluation/store.py`。

workflow tables 是 long-running state layer：

- 用途：保留 market discovery、snapshots、resolution parsing、forecast runs、
  evidence、outcomes 的完整生命周期。
- 使用入口：`--show-workflow`、`--show-market`、`--show-due`。
- 实现文件：`src/beatodds/evaluation/workflow_store.py`。

workflow replay artifacts 是文件副本层：

- 用途：把一次 forecast workflow 的 market、snapshot、parser output、queries、
  evidence、forecast output 复制成可重建文件。
- 默认目录：`workflow_records/`。
- 输出格式：`*.json` 完整结构化记录，`*.md` 人工阅读摘要。
- Git 策略：目录下运行记录被 `.gitignore` 忽略，只提交 README 和 ignore 规则。
- 可用 `WORKFLOW_RECORDS_DIR=/path/to/dir` 覆盖保存位置。

paper trading tables 是 account/ledger layer：

- 用途：保存 paper trading 账户、风险参数和现金流水。
- 使用入口：`scripts/run_paper_account.py`。
- 实现文件：`src/beatodds/evaluation/paper_store.py`。

### 当前本地表计数

2026-06-03 从本地 DuckDB 观测到：

| Table | Rows | 用途 |
|---|---:|---|
| `events` | 40 | 当前最新流动 market 批次关联的 Gamma events |
| `markets` | 350 | 本地累计 market 元数据；GUI 只读最新 `fetched_at` 批次 |
| `latest markets` | 100 | 最新一次 backfill 批次的 market 数 |
| `events with icon` | 37 | 可用于 GUI event icon 的 Gamma event |
| `markets with outcome prices` | 100 | 最新批次已有 YES/NO token display price |
| `eval_records` | 14 | 之后计算 BSS 的 compact forecast samples |
| `tracked_markets` | 1 | 长期追踪的市场状态 |
| `market_snapshots` | 3 | 不可变盘口快照 |
| `resolution_features` | 1 | 解析后的 resolution/search features |
| `forecast_runs` | 2 | 完整 forecast run 记录 |
| `workflow_evidence_items` | 27 | forecast run 关联证据 |
| `outcomes` | 0 | 已解决 outcome |
| `paper_accounts` | 1 | paper trading 账户；本地默认 demo account |
| `paper_account_transactions` | 1 | account cash ledger；默认账户创建流水 |

### compact eval records

`uv run scripts/run_batch_eval.py --show-stored` 可查看当前 compact records。
2026-06-03 本地共有 14 条 `eval_records`。下面是早期 live run 的示例片段：

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
n = 14
mean_edge = -0.0188
mean_abs_edge = 0.0249
max_edge = +0.0305
min_edge = -0.1105
pct |edge| > 3% = 35.7%
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
market_snapshots = 3
forecast_runs = 2
evidence_items = 27
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
  2028 US presidential election candidates
  2028 election polls JD Vance
```

已存 snapshot：

```text
snapshot_time = 2026-05-25T11:54:15.453810
p_m = 0.190
bid = 0.189
ask = 0.190
spread = 0.001
flags = refresh
```

最新 forecast run：

```text
run_id prefix = 9a8c9fdf
signal_type = search_only_llm
model = deepseek-chat
p_m = 0.190
p_f = 0.150
edge = -0.040
confidence = 0.30
```

最新 evidence 样例：

| score | query | source/title |
|---:|---|---|
| 1.000 | `2028 election polls JD Vance` | `newsweek.com: JD Vance's 2028 presidential election chances plunge in new poll` |
| 1.000 | `2028 election polls JD Vance` | `zogbyanalytics.com: JD Vance’s Chances of Beating Harris, Newsom in 2028 Election` |
| 1.000 | `2028 election polls JD Vance` | `sports.yahoo.com: Kamala Harris’ Chances of Beating JD Vance, New 2028 Poll Shows` |
| 0.999 | `2028 US presidential election candidates` | `ballotpedia.org: Presidential candidates, 2028` |
| 0.999 | `2028 US presidential election candidates` | `ballotpedia.org: List of registered 2028 presidential candidates` |

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
- `event_type`
- `china_relevance`
- `key_entities_json`
- `search_queries_json`
- `geography_json`
- `resolution_source_hint`
- `source_routing_hints_json`
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
- `provider`
- `source_type`
- `direction`
- `strength`
- `resolution_relevance`
- `reliability_prior`
- `dedupe_key`
- `raw_metadata_json`

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

`paper_accounts`：

- `account_id`
- `name`
- `base_currency`
- `initial_cash`
- `cash_balance`
- `reserved_cash`
- `status`
- `risk_profile`
- `sizing_mode`
- `order_fraction`
- `auto_trade_enabled`
- `max_order_notional`
- `max_market_exposure`
- `max_event_exposure`
- `max_category_exposure`
- `max_total_exposure`
- `min_cash_buffer`
- `fee_rate_bps`
- `slippage_bps`
- `created_at`
- `updated_at`
- `notes`

`paper_account_transactions`：

- `transaction_id`
- `account_id`
- `transaction_type`
- `cash_delta`
- `reserved_delta`
- `cash_before`
- `cash_after`
- `reserved_before`
- `reserved_after`
- `ref_type`
- `ref_id`
- `memo`
- `created_at`

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

Paper trading account store：

- `create_paper_account(...)`
- `ensure_default_paper_account(initial_cash=10000.0)`
- `load_paper_account(account_id='demo')`
- `load_paper_accounts(limit=50)`
- `update_risk_params(account_id='demo', ...)`
- `deposit_cash(account_id, amount, memo='')`
- `withdraw_cash(account_id, amount, memo='')`
- `reserve_cash(account_id, amount, memo='')`
- `release_reserved_cash(account_id, amount, memo='')`
- `adjust_cash(account_id, cash_delta, memo='')`
- `load_account_transactions(account_id='demo', limit=50)`
- `account_summary()`

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
- `src/beatodds/evidence/retriever.py`：provider-based evidence retrieval。
- `src/beatodds/evidence/china_query.py`：中文 query expansion。
- `src/beatodds/evidence/china_sources.py`：中国 source registry loader。
- `src/beatodds/evidence/china_router.py`：中国 source router。
- `src/beatodds/evidence/providers/`：Tavily 和 mock search provider。
- `src/beatodds/evidence/forecaster.py`：LLM probability forecast。

评分：

- `src/beatodds/baselines/market_only.py`：market-only baseline。
- `src/beatodds/calibrator/ranker.py`：net edge 和 opportunity ranking。

评测和状态：

- `src/beatodds/evaluation/store.py`：compact `eval_records` storage。
- `src/beatodds/evaluation/workflow_store.py`：long-running workflow database。
- `src/beatodds/evaluation/paper_store.py`：paper trading accounts 和 cash ledger。
- `src/beatodds/evaluation/metrics.py`：Brier/log-loss metrics。
- `scripts/run_batch_eval.py`：主 live evaluation CLI。
- `scripts/run_paper_account.py`：paper account CLI。

测试：

- `tests/test_smoke.py`：核心 import 和 scoring smoke test。
- `tests/test_eval_store.py`：compact eval store roundtrip 和 resolution。
- `tests/test_workflow_store.py`：workflow DB market/snapshot、forecast/evidence、
  outcome、due-market tests。
- `tests/test_paper_store.py`：paper account、risk params、cash ledger tests。

参考资料：

- `ref/`：外部参考库的轻量 README pointer tree。应保持小体积，不应放完整上游镜像。

长期上下文：

- `AGENTS.md`：当前 milestone plan、操作约束、已验证命令、下一步工程计划。

## 当前缺口

重要缺口：

- 没有自动 outcome resolver。
- 没有 `eval_metrics` 表。
- 只有轻量 schema migration：`markets.event_id`、`markets.outcome_prices_json`、
  `events.image`、`events.icon` 可自动补列；还没有完整版本化 migration layer。
- 没有 `--forecast-due` 命令。
- 没有 tracked due markets 的 current-price refresh。
- 没有 prompt hash 或 raw prompt storage。
- 没有 empirical calibration 或 market+LLM ensemble。
- 没有单独的 durable scan-run table。
- 没有 paper orders/fills/positions/marks/settlements。

下一步工程：

1. 实现 `paper_orders` 和基于 CLOB depth 的 `paper_fills`。
2. 从 fills 聚合 `paper_positions`，并用 live CLOB 写 `paper_marks`。
3. 为 tracked due markets 刷新当前 order-book snapshots。
4. 在 snapshot refresh 后增加 `--forecast-due`。
5. 增加完整、版本化的本地 DuckDB schema migration safety。
