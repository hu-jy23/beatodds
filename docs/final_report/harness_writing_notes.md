# Harness Writing Notes from AIA Forecaster and MiroFlow

This note records how two reference papers describe their harness / agent framework, and how BeatOdds should borrow the writing pattern.

## AIA Forecaster

Source checked:

- `/mnt/d/Study/AI in Quant/Project/ref/论文/AIA Forecaster原文.pdf`

Writing pattern:

1. Start from the human forecasting process: evidence gathering is iterative, and one source can trigger many follow-up queries.
2. Present the system architecture before implementation details.
3. Decompose the forecaster into named modules:
   - independent forecasting agents;
   - adaptive agentic search;
   - reasoning traces and initial probabilities;
   - supervisor reconciliation;
   - statistical correction / calibration.
4. Give a compact mathematical abstraction:
   - individual search trajectory: `q -> E1 -> E2 -> ... -> En -> (Ri, pi)`;
   - supervisor: `(R1, ..., RM) -> Esupervisor -> pfinal`.
5. Evaluate design choices through benchmarks and ablations:
   - search matters;
   - independent forecasts are unstable;
   - simple ensemble is strong;
   - supervisor should resolve disagreement through additional search;
   - calibration/extremization addresses probability attenuation.

Implication for BeatOdds:

- Our harness section should explicitly define the evidence trajectory, not only list tools.
- We should describe market prior de-emphasis and final calibration as separate stages.
- For multi-run validation, we should report disagreement, consensus, and supervisor/aggregator behavior.
- We should avoid claiming forecast accuracy from case studies; AIA separates architecture, benchmark protocol, and calibration.

## MiroFlow

Sources checked:

- `/mnt/d/Study/AI in Quant/Project/ref/论文/MiroFlow/MiroFlow.pdf`
- `/mnt/d/Study/AI in Quant/Project/ref/论文/MiroFlow/sec/3_method.tex`
- `/mnt/d/Study/AI in Quant/Project/ref/论文/MiroFlow/sec/4_experiment.tex`

Writing pattern:

1. Define the framework as layers:
   - control tier: orchestration, logs, checkpoints, budget, heavy reasoning;
   - agent tier: agent nodes, prompts, context, toolsets, sub-agents, I/O processors;
   - foundation tier: model backends, tools, processors, execution resources.
2. Define agent nodes formally:
   - description;
   - prompt;
   - sub-agents;
   - tools;
   - input/output processors.
3. Define agent graph:
   - main agent as entry;
   - directed graph topology;
   - parallelism and dependencies;
   - declare-then-define workflow.
4. Define heavy-reasoning mode:
   - ensemble policy;
   - verification policy;
   - budget-limited activation.
5. Define robust workflow:
   - message normalization;
   - retry/fallback/replay;
   - fault isolation;
   - semantic error artifacts.
6. Experiments include benchmarks plus ablations:
   - robustness components;
   - heavy-reasoning settings;
   - single-agent vs multi-agent;
   - max-turn budget;
   - tool set;
   - I/O processing.

Implication for BeatOdds:

- Our paper should describe BeatOdds as a forecasting harness with three layers:
  - control/workspace layer;
  - agent/source layer;
  - foundation/tool/data layer.
- We should define the agent workspace and markdown protocol as the control plane.
- We should define source-specific skills as reusable foundation-layer components.
- We should explicitly discuss robustness: candidate-pool retention, resource locks, ASR fallback, timeouts, and coverage gaps.
- Our evaluation section should include ablation plans, even if not all are finished.

## Updated BeatOdds Framing

Use the following structure in the final paper:

1. Forecasting task abstraction.
2. System architecture figure.
3. Agentic evidence trajectory:
   `market question + resolution rule -> workspace -> think_k -> action_k -> artifact_k -> think_{k+1} -> final report`.
4. Multi-agent execution:
   - main forecasting agent;
   - source processors;
   - video render sub-agents;
   - optional multi-run ensemble.
5. Robustness and auditability:
   - candidate pools;
   - full trajectory appendix;
   - source cards;
   - database-backed run state;
   - failed-tool artifacts.
6. Evaluation:
   - market-only baseline;
   - search-only LLM;
   - market+LLM ensemble;
   - ablations on search, source depth, market-prior timing, heavy reasoning, multi-agent runs.

The final report should no longer frame the second research direction as only China-specific source access. The more general contribution is social-media-augmented forecasting. China-related cases are the strongest demonstration setting because many relevant priors are embedded in semi-public social context, professional conventions, video commentary, and long-running community knowledge.

Social media should be described as a source of human intermediate reasoning. The agent searches raw information, but it also digests artifacts produced by humans who have already searched, filtered, interpreted, and formed posterior beliefs. The system contribution is to make this digestion auditable: candidate pools, engagement signals, author context, content depth, source cards, and final evidence use/rejection decisions.
