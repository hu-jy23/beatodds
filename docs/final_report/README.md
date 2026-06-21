# BeatOdds Final Report Draft

This directory contains a NeurIPS-style final project report scaffold.

## Template Sources

- `template_sources/overleaf_neurips_2026_page.html`: saved Overleaf NeurIPS 2026 template page.
- `template_sources/neurips_2026_instructions.pdf`: saved PDF instructions from the Overleaf template page.
- `neurips_2026.sty`: local fallback style file for compilation. Replace it with the official NeurIPS 2026 author-kit style file before final submission if the official download becomes available.

Useful references checked on 2026-06-18:

- Overleaf NeurIPS 2026 template: https://www.overleaf.com/latex/templates/formatting-instructions-for-neurips-2026/bjdwqfdkyftc
- TypeTeX NeurIPS 2026 template notes: https://www.typetex.app/templates/neurips
- TypeTeX style-file notes: https://www.typetex.app/templates/neurips/style-file

## Current Writing Plan

The paper is organized around the scientific question: can a stateful agentic forecasting system detect and evaluate mispricing in prediction markets under realistic information constraints?

Planned sections:

1. Abstract: one-paragraph summary of the problem, system, evaluation protocol, and preliminary evidence.
2. Introduction: motivate prediction market mispricing, leakage-free forecasting, and agentic information search.
3. Problem Setting: define markets, events, resolution rules, forecasts, baselines, and live evaluation.
4. System Overview: explain scanner, database, agent workspace, evidence tools, forecaster, calibrator/ranker, GUI, and paper trading.
5. Agentic Evidence Harness: describe md-defined agent loops, source-specific skills, trajectory capture, and China-specific source access.
6. Stateful Data and Trading Layer: describe DuckDB state, eval records, paper accounts, orders, fills, positions, and auditability.
7. Baselines and Evaluation: define market-only, search-only LLM, market+LLM ensemble, Brier/BSS, live resolution tracking.
8. Case Studies: Taiwan 2026, Xi before 2027, Best Chinese AI Company, plus GUI/trading demonstrations.
9. Discussion: empirical lessons, source quality, market priors, calibration, and operational failure modes.
10. Limitations and Ethics: uncertainty, market manipulation risk, social media reliability, and responsible use.

## Compile

```bash
cd docs/final_report
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```
