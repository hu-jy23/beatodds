# 给 GPT 的 BeatOdds 项目上下文报告

状态日期：2026-06-05
代码库：`/mnt/d/Study/AI in Quant/Project/beatodds`
远程仓库：`https://github.com/hu-jy23/beatodds.git`
当前分支：`master`
当前状态：`master...origin/master`，工作区干净
最新提交：`8671e68 Add paper trading accounts and user dashboard`

## 1. 项目目标

BeatOdds 是一个面向 Polymarket 的错价发现与实时评测系统。核心目标来自
`BeatOdds_midterm.pdf` 第三、第四节：构建 agent workflow，发现市场价格
`p_m` 和系统公平概率 `p_f` 之间的差异。

核心公式：

```text
edge = p_f - p_m
```

- `p_m`：Polymarket 当前盘口隐含概率。
- `p_f`：系统基于 resolution 语义、外部证据、LLM forecast、市场结构得到的公平概率。
- `edge > 0`：系统认为 YES 被低估。
- `edge < 0`：系统认为 YES 被高估。

当前科学评估路线是 live forward evaluation。系统在预测时冻结 evidence，保存预测、盘口、证据和模型输出，等待市场 resolve 后写入 outcome，再计算 Brier / Brier Skill Score。这样可以避免历史回测中的 evidence leakage。

## 2. 当前总体状态

当前仓库已经不是空原型，已有一条可以运行的 live workflow：

1. 从 Polymarket Gamma API 拉取 event / market 元数据。
2. 从 Polymarket CLOB 读取实时订单簿。
3. Scanner 生成候选市场。
4. Resolution parser 用 LLM 抽取结构化 resolution 条件。
5. Evidence retriever 用 Tavily 检索外部证据，并在检索前冻结时间。
6. Forecaster 用 DeepSeek / OpenAI-compatible LLM 输出 `p_f`、confidence、reasoning。
7. Workflow store 把每次 run 的 market、snapshot、features、evidence、forecast、eval record 写入 DuckDB。
8. GUI 展示 event-centric 市场界面、订单簿、forecast、evidence、paper account 和用户页。

现阶段最大缺口是长期交易账本和科学评估的闭环尚未完全完成。账户系统已经有基础账本，正式的 paper orders / fills / positions / settlements 还没有完成。评估数据库已有 unresolved records，仍需要未来 outcome 来计算真正的 BSS。

## 3. 主要目录和模块

### `src/beatodds/data/`

负责 Polymarket 数据层。

- `gamma_client.py`：读取 Gamma API，解析 event / market。
- `clob_client.py`：读取 CLOB order book，生成 `PriceSnapshot`。
- `indexers.py`：增量 backfill market / event index。
- `storage.py`：把 market index 写入 `data/beatodds.duckdb`。

### `src/beatodds/scanner/`

负责 live market scanner。

- 拉取 active/liquid markets。
- 过滤过近 deadline、低质量市场。
- 标注 neg-risk、multi-outcome、low-volume、wide-spread。
- 输出候选 `CandidateMarket`。

入口：

```bash
uv run scripts/run_scanner.py --top 10
uv run scripts/run_forecast.py --top 5 --dry-run
```

### `src/beatodds/relation_miner/`

负责简单结构套利检测。

- YES / NO bundle consistency。
- neg-risk group sum consistency。
- 可选补全完整 event group。

当前只覆盖直接结构约束，复杂 implication graph 暂缓。

### `src/beatodds/resolution_parser/`

负责解析 market question 和 resolution text。

当前输出：

- `condition_type`
- `key_entities`
- `deadline`
- `oracle_type`
- `search_queries`
- `ambiguity_score`
- `risk_flags`

China-specific info search 应在这里扩展 China relevance、event type、source routing hints，而不是另写一套 parser。

### `src/beatodds/evidence/`

负责 evidence retrieval 和 forecast。

- `retriever.py`：当前是 Tavily-only retriever。会在 search call 前写入 `evidence_frozen_at`，并丢弃发布时间晚于 frozen time 的证据。
- `forecaster.py`：把 market、resolution、`p_m`、evidence 输入 LLM，输出 `p_f`、confidence、reasoning。

当前限制：

- 尚未保存完整 prompt 或 prompt hash。
- China-specific source routing 尚未实现。
- Evidence provider abstraction 尚未实现。

### `src/beatodds/calibrator/`

负责把 forecast 和结构信号转成机会排序。

- `ranker.py`：计算 edge、net edge、排序。

当前限制：

- 还没有 empirical calibration。
- 还没有正式的 Market+LLM ensemble baseline。

### `src/beatodds/evaluation/`

负责评估数据库和长期 workflow state。

- `store.py`：compact `EvalRecord` 存储，用于 Brier / BSS。
- `workflow_store.py`：完整 workflow DB，保存 tracked markets、snapshots、resolution features、forecast runs、evidence、outcomes。
- `paper_store.py`：paper trading account ledger，保存账户、资金、风险参数、交易流水。

### `gui/`

负责本地研究界面。

- `gui/server.py`：本地 HTTP API 和静态文件服务。
- `gui/web/app.js`：前端交互。
- `gui/web/styles.css`：前端样式。

启动：

```bash
uv run scripts/run_gui.py --host 127.0.0.1 --port 8765 --open
```

访问：

```text
http://127.0.0.1:8765
```

## 4. 数据库现况

本地 DuckDB 在 `data/` 下，已被 `.gitignore` 忽略，不应提交。

### `data/beatodds.duckdb`

这是市场索引数据库。

主要表：

- `events`
- `markets`
- `price_snapshots`
- `price_history`
- `resolutions`
- `evidence_items`
- `edge_scores`

当前已观察状态：

- 约 40 个 events。
- 约 350 个 cumulative markets。
- 最新 backfill 的 100 个 markets 有 outcome prices。
- 大多数 events 有 icon / image，可用于 GUI event card。

### `data/eval.duckdb`

这是 workflow / evaluation / paper account 数据库。

主要 workflow 表：

- `tracked_markets`
- `market_snapshots`
- `resolution_features`
- `forecast_runs`
- `workflow_evidence_items`
- `eval_records`
- `outcomes`

当前已观察状态：

- 14 条 unresolved compact eval records。
- 1 个 tracked market。
- 3 条 market snapshots。
- 2 条 forecast runs。
- 27 条 workflow evidence items。
- 0 条 outcomes。

当前 tracked market：

```text
condition_id:
0x7ad403c3508f8e3912940fd1a913f227591145ca0614074208e0b962d5fcc422

question:
Will JD Vance win the 2028 US Presidential Election?

latest stored forecast:
p_m = 0.190
p_f = 0.150
edge = -0.040
confidence = 0.30
model = deepseek-chat
```

主要 paper account 表：

- `paper_accounts`
- `paper_account_transactions`

账户系统已支持：

- 创建默认 demo account。
- 创建本地用户账户。
- 设置显示名、头像文字、备注。
- 设置初始资金、现金余额。
- 设置 sizing mode、order fraction、fee / slippage、单笔风险、单市场风险、现金 buffer、自动 paper order 开关。
- deposit / withdraw。
- reserve / release cash。
- account summary。
- transaction history。

当前未完成：

- `paper_orders`
- `paper_fills`
- `paper_positions`
- mark-to-market NAV。
- resolve 后 settlement。
- 正式把 agent signal 转成订单生命周期。

## 5. GUI 当前状态

GUI 已从 market-centric 改成 event-centric，参考 Polymarket 的组织逻辑。

核心层级：

```text
event -> market -> YES/NO token side -> order book / forecast / evidence
```

主要界面：

- 顶栏是全局页面顶栏。
- `Markets` 页面：左侧 event list，中部 event detail 和 event 下 markets，右侧 selected market workbench。
- `User` 页面：独立用户页，不把账户功能堆在 market 页面。

Markets 页面：

- 左侧展示 events。
- 中部展示 selected event，包括 icon、tags、title、markets、rules/background tabs。
- 每个 market 有 YES / NO token buttons。
- 右侧展示 selected market 的 live price、forecast、evidence、chart、CLOB order book。
- Order book 区域可滚动查看所有返回档位，不画累计深度曲线。
- 支持 dark / light mode。

User 页面：

- 登录方式很轻量：列出已有 paper accounts，点击按钮切换。
- 创建用户只需要 name。
- 左侧 sidebar 包括 Overview、基础设置、资金管理、持仓与交易、Agent Pattern。
- 当前持仓和历史交易按 event -> market tree 展示。
- Event 默认折叠，点击 event 展开 markets，再点击收回。
- 当前持仓/交易来自 GUI simulated paper deals 聚合，还不是正式 order/fill/position 表。

## 6. Paper Trading 状态

当前已经完成账户和资金账本基建。

已实现：

- `PaperAccount`
- `PaperAccountTransaction`
- `PaperStore`
- `paper_accounts`
- `paper_account_transactions`
- 用户切换和创建。
- 资金充值/减少。
- 风险参数设置。
- GUI 读取账户配置估算 simulated paper deal 的 notional、shares、fee、projected PnL。

尚未实现正式交易生命周期：

```text
signal -> proposed order -> risk check -> order -> fill -> position -> mark -> exit/settlement -> realized PnL
```

下一步应补：

- `paper_orders`
- `paper_fills`
- `paper_positions`
- `paper_marks`
- `paper_settlements`
- 订单状态机。
- 账户 NAV 曲线由真实 ledger 和 marks 生成。

## 7. China-Specific Info Search 规划状态

项目根目录有一份已适配当前仓库的文档：

```text
/mnt/d/Study/AI in Quant/Project/china_info_agent_codex_goal.md
```

该文档已经删除 GPT 原稿中不适合当前状态的内容，并重新对齐到 BeatOdds 现有架构。

结论：

China-specific info search 应作为当前 evidence pipeline 的增强层实现，不应新建平行 agent 项目。

应接入的位置：

- `ResolutionParser`：增加 China relevance、event type、source hints。
- `EvidenceRetriever`：从 Tavily-only 改成 provider-based retriever，Tavily 保留为 baseline。
- `workflow_store`：保存 provider、source_type、evidence direction、strength、reliability、latency、coverage。
- `run_batch_eval.py`：增加例如 `--china-info` 的 flag，同时保存 baseline evidence 和 china-enhanced evidence。

第一阶段不做：

- 不新建独立 `src/china_info/` 平行项目。
- 不做 Baidu / 360 / Sogou / Weixin 全量 crawler。
- 不绕过登录、验证码、付费墙、反爬限制。
- 不一开始做 30+ historical backtest。
- 不直接做复杂 probability adjuster。

建议 milestone：

1. M1：China relevance and event typing。
2. M2：Chinese query expansion。
3. M3：Source registry and router。
4. M4：Provider abstraction，Tavily baseline + site/domain search + mock fixtures。
5. M5：官方来源页面和发布时间抽取。
6. M6：Evidence annotation / scoring。
7. M7：Workflow DB persistence。
8. M8：CLI integration。
9. M9：Coverage / latency / forecast delta evaluation。

## 8. 当前 Baselines 状态

已实现或部分实现：

- Market-only baseline：`src/beatodds/baselines/market_only.py`。
- Search-only LLM：已有 evidence + LLM forecast 组件，可通过不使用结构信号来近似。
- Market+LLM：当前 forecast prompt 使用 `p_m` 和 evidence，但还没有正式 ensemble calibrator。

仍需补齐：

- 可重复运行的 baseline comparison CLI。
- Market-only / Search-only / Market+LLM 的统一 evaluation table。
- resolved outcomes 后的 Brier / BSS 分组统计。
- 按 market type、source type、event type 的分层评价。

## 9. 当前开发约束

需要遵守：

- 不提交 `.env`。
- 不提交 `data/`。
- 不提交 `.venv/`。
- 不提交完整外部 reference repo 镜像。
- DuckDB 写入要串行，不要并发跑两个写 `data/eval.duckdb` 的命令。
- Tavily baseline 必须保留，China-specific 检索只能作为增强层加入。
- Evidence retrieval 必须保留 frozen time 边界。
- LLM 只能基于保存的 evidence 预测，不能用预测时间之后的信息。
- GUI account 操作目前进入 paper account ledger；GUI simulated deal 还不是正式订单。

## 10. 常用命令

环境：

```bash
uv sync --extra dev
```

测试：

```bash
uv run ruff check .
uv run pytest -q
node --check gui/web/app.js
```

GUI：

```bash
uv run scripts/run_gui.py --host 127.0.0.1 --port 8765 --open
```

市场 backfill：

```bash
uv run scripts/backfill_markets.py --incremental
```

Scanner：

```bash
uv run scripts/run_scanner.py --top 10
uv run scripts/run_scanner.py --top 10 --complete-groups
```

Batch eval：

```bash
uv run scripts/run_batch_eval.py --top 5 --exclude-sports --min-prob 0.05
uv run scripts/run_batch_eval.py --show-stored
uv run scripts/run_batch_eval.py --show-workflow
uv run scripts/run_batch_eval.py --show-market <condition_id>
uv run scripts/run_batch_eval.py --show-due --stale-hours 24 --top 10
uv run scripts/run_batch_eval.py --compute-bss
```

Paper account：

```bash
uv run scripts/run_paper_account.py --create-default
uv run scripts/run_paper_account.py --show --transactions
```

## 11. 给 GPT 的工作方式要求

如果 GPT 要继续给建议，应基于以上状态，不要重复建议已经实现的内容。

建议输出应满足：

1. 先判断当前系统已有能力。
2. 再指出真正缺口。
3. 每个 proposal 都映射到现有文件。
4. 优先兼容现有 workflow DB 和 GUI。
5. 优先小步可验收，不要提出大而散的全新系统。
6. 对 China-specific info search，必须先保留 Tavily baseline，再加 China-enhanced branch。
7. 对 paper trading，必须从账户 ledger 继续扩展订单、成交、持仓、mark、settlement。
8. 对评估，必须围绕 live forward records、frozen evidence、future outcomes、Brier / BSS。

推荐给 GPT 的开场上下文：

```text
你正在协助 BeatOdds 项目。BeatOdds 已经有 Polymarket live scanner、Gamma/CLOB 数据层、
resolution parser、Tavily evidence retriever、DeepSeek/OpenAI-compatible forecaster、
workflow DuckDB、eval records、local GUI、paper account ledger。当前目标不是重新设计原型，
而是在现有代码库上补齐 China-specific evidence enhancement、formal paper trading lifecycle、
baseline evaluation 和 live workflow state。请不要建议新建平行项目；所有建议需要映射到
现有文件和可测试 milestone。
```
