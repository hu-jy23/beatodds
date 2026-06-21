# BeatOdds — Polymarket 错价发现系统

---

## 项目目标

BeatOdds 在 Polymarket 上寻找定价偏差。核心公式：

```
edge = p_f - p_m
```

`p_m` 是市场价格，永远是先验基准。`p_f` 是系统估计的公平概率，来源于三类信号：结构性约束、外部证据、分辨率语义。当 `|edge|` 超过点差加手续费时，就是可操作的机会。

### 当前研究主线更新

期中报告中的系统设计主线，基本对应当前 GUI、数据库、paper trading 和评估界面的落地。GUI 不是附属展示层，而是让研究者和使用者围绕 `event → market → evidence → forecast → trade/state` 这条链路检查系统行为的操作界面。它需要呈现 Polymarket 的事件组织方式、市场盘口、forecast 记录、证据轨迹、账户、持仓、交易历史和评估状态，从而把“发现错价”变成一个可审计、可复盘、可长期运行的工作流。

另一条研究主线已经从早期的 China-specific information search，扩展为 **social-media-augmented prediction**。预测主体不再只是一个带搜索工具的 agent；它应当是 `agent + social media multi-source search` 的组合系统。Agent 需要搜索 raw information，也需要消化社交媒体、视频平台、专业社区和自媒体中的半加工材料。这些材料常常是许多人类已经做过大量搜索、吸收领域先验、结合个人经验之后写出的 report。高质量博主、业内人士和长期观察者的内容，本质上包含他们已经充分消化后的先验与后验判断；agent 的任务是发现这些内容，筛选其质量，批判性吸收其中的推理链，再形成自己的概率估计。

这条路线在中国相关事件上尤其重要。很多中国语境下的关键信息并不会完整写在官方文件、新闻稿或结构化数据库里，而是隐藏在大环境、行业默认、公开秘密、圈内共识和长期语境之中。许多认知处在半公开状态：如果没有进入对应领域、没有长期接触相关社区，就很难知道这些信息存在。Social-media-augmented prediction 的价值就在于让 agent 通过视频、微博、知乎、雪球、公众号、专业论坛、研报和新闻社等多源材料，吸收这些分散在人类社区中的隐性知识，并把它们转化为可记录、可审计、可质疑的 forecast evidence。

---

## 我们如何构建

### 第一步 — 市场数据接入：`py-clob-client-v2`

首要问题是获取 Polymarket 数据。Polymarket 有两个 API：
- **CLOB**（中央限价订单簿）：实时买卖价、订单簿深度
- **Gamma API**：市场元数据、描述文本、事件分组

我们从 [`ref/official-polymarket/py-clob-client-v2`](./ref/official-polymarket/py-clob-client-v2/) 出发——Polymarket 官方最新 Python SDK。参考自：`nautilus_trader` 的 Polymarket 适配器内部就是 `from py_clob_client_v2.client import ClobClient`，v2 是 latest 版本。

封装在 `src/beatodds/data/clob_client.py`。注意 **CLOB v2 SDK 返回的订单簿两侧排序与常规相反**——`bids` 升序排列（best bid = 最后一个元素），`asks` 降序排列（best ask = 最后一个元素）。盘口使用 `bids[-1]` / `asks[-1]`。

Gamma API 的市场发现参考了 [`ref/official-polymarket/agents`](./ref/official-polymarket/agents/) 中指向的 `agents/polymarket/gamma.py`——这是解析 Polymarket 事件/市场结构（含 `clobTokenIds` JSON 字符串数组、`outcomePrices`）最完整的参考实现。

### 第二步 — 数据持久化：`prediction-market-analysis` 模式

原始市场数据需要落盘，用于分析和回测。我们参考了 [`ref/data-backtesting/prediction-market-analysis`](./ref/data-backtesting/prediction-market-analysis/)（Jon Becker 的 indexer）的存储模式：**DuckDB + Parquet 分块**。

- Parquet 用于批量历史数据（支持断点续传、列式压缩）
- DuckDB 直接 SQL 查询 Parquet 文件，无需全量加载到内存
- 实现在 `src/beatodds/data/storage.py` 和 `common/db.py`

Indexer 支持全量回填（`run_backfill`）和增量更新（`run_incremental`）。Bug：直接调用 `/events?order=id` 接口返回的全是 5 分钟加密货币涨跌微市场（volume=0，毫无价值）。修复：改用 `/markets?order=volume24hr&volume_num_min=100` 只拉有流动性的市场，封装为 `GammaClient.get_liquid_markets()`。

### 第三步 — 市场扫描器

数据流通之后，Scanner（`src/beatodds/scanner/scanner.py`）负责筛选值得分析的市场：

- 从 Gamma 拉取 100–500 个流动性市场
- 对每个市场调用 CLOB 取订单簿快照（bid/ask/midpoint）
- 按优先级打分：neg_risk 市场加分（结构约束更多），点差紧的市场加分，点差宽的扣分
- 输出 `list[CandidateMarket]`，全程 pydantic v2 强类型，无裸 dict

点差惩罚机制很重要：点差 > 10¢ 的市场即使发现 edge 也很难成交。

### 第四步 — 结构性套利检测：`polymarket-arbitrage` 逻辑

RelationMiner（`src/beatodds/relation_miner/miner.py`）检测不需要外部信息、纯靠数学就能发现的结构性错价。

核心逻辑参考了 [`ref/strategy-execution/polymarket-arbitrage`](./ref/strategy-execution/polymarket-arbitrage/)（ImMike 的 bundle 套利扫描器）：

**二元市场 bundle 套利**：每个 Polymarket 市场都有 YES 和 NO token，买 1 YES + 1 NO 保证在结算时收到恰好 $1。因此：
- `YES_ask + NO_ask < 1 - fees` → **bundle long**（买入双边，锁定利润）
- `YES_bid + NO_bid > 1 + fees` → **bundle short**（卖出双边）

**neg_risk 组套利**：Polymarket 还有 "neg_risk" 市场，多个结果共享同一底仓（如 "谁赢世界杯"）。同组所有 YES 价格之和必须等于 1.0——只有一支队伍能赢。若 `sum > 1 + fees` 可卖空，若 `sum < 1 - fees` 可全买。

一个复杂之处：Scanner 只按交易量取前 N 名市场，neg_risk 组可能不完整（例如 Knicks 排在前 100 之外，NBA Finals 组少了一员）。解法：`GammaClient.get_event_markets(event_id)` 按事件 ID 额外拉取完整组。NBA Finals 验证结果：OKC(64.5%) + Spurs(15%) + Knicks(18.3%) + Cavs(2.05%) = 99.85% ≈ 1.0，定价正确，无误报。

### 第五步 — 分辨率文本解析

要搜索相关证据，首先要理解*市场究竟在问什么*。ResolutionParser（`src/beatodds/resolution_parser/parser.py`）用 LLM 从分辨率文本中提取：

- `condition_type`（价格阈值？体育结果？选举？）
- `key_entities`（命名实体：人名、代码、国家）
- `search_queries`（2–4 条 Tavily 搜索查询）
- `ambiguity_score` 和 `risk_flags`（分辨率标准是否清晰？）

Prompt 设计参考了 [`ref/agent-benchmark/FutureShow`](./ref/agent-benchmark/FutureShow/)（HKUDS 的多源证据 agent），它对 "把市场问题转化为搜索查询" 有最结构化的处理。

支持 **Anthropic（Claude haiku）**、**DeepSeek（deepseek-chat）**、**OpenAI（gpt-4o-mini）** 三个后端，从 `.env` 自动选择，优先级：Anthropic > DeepSeek > OpenAI。

### 第六步 — 证据检索：Tavily

EvidenceRetriever（`src/beatodds/evidence/retriever.py`）将第五步生成的搜索查询发给 Tavily 新闻搜索 API。

**关键设计**：`evidence_frozen_at` 在任何搜索调用**之前**就已设置好。这个时间戳是时间完整性边界——不使用此时刻之后发布的任何证据。这防止了回测中的前视偏差（T 时刻的市场快照不能使用 T+1 的新闻）。

每条证据记录其 `published_at` 时间；来自未来的条目会被丢弃。`evaluation/metrics.py` 中的 `check_temporal_integrity()` 可对一批评估记录批量验证此不变量。

> 注：实时模式下扫描（取快照）比搜索早约 30–60 秒，因此时间完整性检查会出现 "误报"，这是正常现象。`--backtest` 标志用于严格校验回测场景。

### 第七步 — LLM 概率预测

LLMForecaster（`src/beatodds/evidence/forecaster.py`）接收市场信息、分辨率条件和检索到的证据，输出 `p_f`——我们的概率估计值。

Prompt 中的关键约束：*"仅基于所提供的证据给出估计——不使用训练数据中的背景知识。"* 这让模型锚定在检索到的事实上，而非凭训练数据幻觉。

输出 `ForecastResult` 包含 `confidence`（偏离 `p_m` 的强度）和 `reasoning`（一句话解释）。

### 第八步 — 信号融合与排名

Ranker（`src/beatodds/calibrator/ranker.py`）将结构性信号（来自 RelationMiner）和证据信号（来自 Forecaster）合并成最终的 `EdgeScore`：

```
net_edge = |p_f - p_m| - spread/2 - taker_fee_rate
```

按 `|net_edge| × confidence` 降序排名。结构性违规的 confidence=0.8（数学是确定的）；LLM 预测的 confidence 取决于证据质量。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                        数据层                               │
│  GammaClient ──► MarketIndexer ──► DuckDB + Parquet         │
│  ClobReadClient（已修正 bid/ask 排序）                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ CandidateMarket 列表
┌──────────────────────────▼──────────────────────────────────┐
│                       扫描器                                │
│  优先级评分：neg_risk 加分、点差惩罚、成交量加成            │
└──────────┬───────────────────────────┬──────────────────────┘
           │                           │
           ▼                           ▼
┌──────────────────┐        ┌──────────────────────────────────┐
│  RelationMiner   │        │        信号管线                  │
│  bundle 套利     │        │  ResolutionParser（LLM cheap）   │
│  neg_risk 完整组 │        │          ↓                       │
│  → violations    │        │  EvidenceRetriever（Tavily）     │
└────────┬─────────┘        │          ↓                       │
         │                  │  LLMForecaster（sonnet/DS-V3）   │
         └──────────────────►          ↓                       │
                            │  ForecastResult（p_f）           │
                            └─────────────┬────────────────────┘
                                          │
                            ┌─────────────▼────────────────────┐
                            │        Ranker / Calibrator       │
                            │  net_edge = |p_f - p_m|          │
                            │            - spread/2 - fee      │
                            │  按 net_edge × confidence 排名   │
                            └──────────────────────────────────┘
```

## 工作流：一个完整扫描周期

```
1. [每 5 分钟]    Scanner 从 Gamma API 拉取 100–500 个流动性市场
                  → 调用 CLOB 取快照（bid/ask/midpoint）
                  → 按成交量 + 剩余天数过滤
                  → 输出 CandidateMarket 列表

2. [立即]         RelationMiner 检查结构性约束
                  → bundle 套利：YES_ask + NO_ask vs 1.0
                  → neg_risk 组：YES 价格之和 vs 1.0
                  → 可选按 event_id 拉取完整组

3. [每 30 分钟]   对优先级最高的 N 个候选市场：
                  → ResolutionParser 提取 key_entities + search_queries
                  → EvidenceRetriever 调用 Tavily（先设 evidence_frozen_at）
                  → LLMForecaster 输出 p_f + confidence

4. [每 1 小时]    Ranker 合并信号 → 排序后的 EdgeScore 列表
                  → 最优机会记录，供人工审查 / paper trading

5. [Phase 3]      通过 polymarket-paper-trader Engine 进行 paper trading
                  → 基于真实订单簿深度模拟成交
                  → 与 market-only baseline 对比追踪 PnL
```

## 参考库汇总

| 库 | 在 BeatOdds 中的作用 | 取用内容 |
|----|---------------------|---------|
| [`py-clob-client-v2`](./ref/official-polymarket/py-clob-client-v2/) | 官方 Polymarket SDK | CLOB 只读客户端、订单簿 API |
| [`agents/polymarket/gamma.py`](./ref/official-polymarket/agents/) | Gamma API 参考实现 | `parse_market()` 字段映射、clobTokenIds 解析 |
| [`prediction-market-analysis`](./ref/data-backtesting/prediction-market-analysis/) | 数据基础设施 | DuckDB+Parquet 存储模式、indexer 结构 |
| [`polymarket-arbitrage`](./ref/strategy-execution/polymarket-arbitrage/) | Bundle 套利逻辑 | 手续费公式、bundle long/short 检测 |
| [`FutureShow`](./ref/agent-benchmark/FutureShow/) | 证据 agent 设计 | 多查询搜索模式、forecast prompt 结构 |
| [`prediction-market-backtesting`](./ref/data-backtesting/prediction-market-backtesting/) | 回测底座 | NautilusTrader Polymarket 适配器（Phase 2+） |
| [`polymarket-paper-trader`](./ref/strategy-execution/polymarket-paper-trader/) | Paper trading 引擎 | 订单簿模拟、手续费模型（Phase 3） |
| [`prediction-market-agent-tooling`](./ref/agent-benchmark/prediction-market-agent-tooling/) | 评估框架 | Brier Score、benchmark harness 概念 |

---

## 快速开始

### 环境准备

```bash
# 安装依赖
cd beatodds
uv sync
```

`ref/` 目录通过 git submodule 固定上游参考仓库版本。首次 clone 后如未自动拉取子模块，运行：

```bash
git submodule update --init --recursive
```

开发和测试环境：

```bash
uv sync --extra dev
uv run pytest -q
```

### 配置

复制 `.env.example` 为 `.env` 并填入密钥：

```bash
cp .env.example .env
```

完整管线所需：
- `TAVILY_API_KEY` — 证据搜索（[tavily.com](https://tavily.com)）
- 以下三选一：
  - `ANTHROPIC_API_KEY` — Claude haiku（解析）+ Claude sonnet（预测）
  - `DEEPSEEK_API_KEY` — deepseek-chat（解析 + 预测，性价比最高）
  - `OPENAI_API_KEY` — gpt-4o-mini（解析）+ gpt-4o（预测）

纯结构性扫描（RelationMiner）无需 LLM 密钥。

### 运行

```bash
# 1. 拉取实时市场数据
uv run scripts/backfill_markets.py --incremental

# 2. 结构性扫描（无需 API 密钥）
uv run scripts/run_scanner.py --top 10

# 2a. 更严格但更慢：补全 neg_risk 事件组
uv run scripts/run_scanner.py --top 10 --complete-groups

# 3. 完整管线：解析 + 检索 + 预测（需要 .env）
uv run scripts/run_forecast.py --top 5

# 3a. 干跑（扫描 + 解析，跳过 Tavily 检索与最终预测）
uv run scripts/run_forecast.py --top 5 --dry-run

# 3b. 回测模式（开启严格时间完整性校验）
uv run scripts/run_forecast.py --top 5 --backtest

# 4. 本地 GUI（市场选择、分析建议、记忆/历史、动态图表）
uv run scripts/run_gui.py --host 127.0.0.1 --port 8765
```

打开浏览器访问 `http://127.0.0.1:8765`。GUI 使用 `data/beatodds.duckdb`
和 `data/eval.duckdb` 读取市场与评估状态，并将界面记忆、跟踪列表、操作历史
和纸面交易记录保存在 `data/gui_state.json`。

---

## 评估协议

按期中报告 §4.2 方法论：

**三条基线**（复杂度递增）：
1. `market_only` — `p_f = p_m`，edge = 0（下限，任何有效信号必须超越此基线）
2. `search_only_llm` — Tavily + LLM，无结构性信号
3. `market_llm_ensemble` — 完整管线

**核心指标**：Brier Skill Score = `1 - BS_ours / BS_market`。正值代表击败市场。

**时间完整性**由 `check_temporal_integrity()` 强制执行：每条 `EvalRecord` 必须满足 `evidence_frozen_at < snapshot_time`（回测模式下）。

---

## 目录结构

```
beatodds/
├── src/beatodds/
│   ├── common/
│   │   ├── types.py          # 13 个 pydantic v2 模型（所有跨模块接口）
│   │   ├── config.py         # Settings（所有阈值、API 密钥、模型名）
│   │   └── db.py             # DuckDB schema + 连接工厂
│   ├── data/
│   │   ├── gamma_client.py   # Gamma API（市场发现、事件分组）
│   │   ├── clob_client.py    # CLOB v2 只读（注意：已修正 bid/ask 排序）
│   │   ├── storage.py        # Parquet 分块 + DuckDB upsert
│   │   └── indexers.py       # 全量回填 + 增量更新
│   ├── scanner/
│   │   └── scanner.py        # 候选市场筛选 + 优先级评分
│   ├── relation_miner/
│   │   └── miner.py          # Bundle 套利 + neg_risk 完整组分析
│   ├── resolution_parser/
│   │   └── parser.py         # LLM 分辨率文本解析（三后端）
│   ├── evidence/
│   │   ├── retriever.py      # Tavily 证据搜索（时间完整性保障）
│   │   └── forecaster.py     # LLM 概率预测（三后端）
│   ├── calibrator/
│   │   └── ranker.py         # 信号融合 + net_edge 排名
│   ├── baselines/
│   │   └── market_only.py    # 基线 1：p_f = p_m
│   └── evaluation/
│       └── metrics.py        # Brier/BSS + 时间完整性检查
├── scripts/
│   ├── backfill_markets.py   # 一次性数据拉取
│   ├── run_scanner.py        # 结构性扫描 + 关系挖掘
│   └── run_forecast.py       # 完整信号管线
├── tests/
├── data/
│   └── raw/                  # Parquet 快照（已 gitignore）
├── pyproject.toml
└── .env.example
```
