"""Write file-based workflow replay artifacts for forecast runs."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from beatodds.common.types import (
    CandidateMarket,
    EvidenceItem,
    ForecastResult,
    ResolutionFeatures,
)

SCHEMA_VERSION = "workflow-record-v1"


def save_workflow_record_copy(
    run_id: str,
    candidate: CandidateMarket,
    features: ResolutionFeatures,
    evidence: list[EvidenceItem],
    forecast: ForecastResult,
    evidence_frozen_at: datetime,
    signal_type: str,
    created_at: datetime,
    records_dir: str | Path | None = None,
) -> Path:
    """Write JSON and Markdown copies that can reconstruct one workflow run."""
    output_dir = _workflow_records_dir(records_dir)
    saved_at = datetime.now(timezone.utc)
    p_m = candidate.snapshot.midpoint
    p_f = forecast.p_f

    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "saved_at": _to_jsonable(saved_at),
        "signal_type": signal_type,
        "market": _to_jsonable(candidate.market),
        "snapshot": _to_jsonable(candidate.snapshot),
        "candidate": {
            "scan_flags": candidate.scan_flags,
            "priority_score": candidate.priority_score,
        },
        "resolution_features": _to_jsonable(features),
        "forecast_run": {
            "condition_id": candidate.market.condition_id,
            "snapshot_time": _to_jsonable(candidate.snapshot.snapshot_time),
            "evidence_frozen_at": _to_jsonable(evidence_frozen_at),
            "p_m": p_m,
            "p_f": p_f,
            "edge": p_f - p_m,
            "confidence": forecast.confidence,
            "signal_type": signal_type,
            "model_version": forecast.model,
            "reasoning": forecast.reasoning,
            "created_at": _to_jsonable(created_at),
        },
        "evidence": [_to_jsonable(item) for item in evidence],
        "query_summary": _query_summary(evidence),
        "source_type_counts": dict(Counter(item.source_type for item in evidence)),
        "provider_counts": dict(Counter(item.provider for item in evidence)),
        "reconstruction_steps": [
            "Load market metadata and CLOB snapshot.",
            "Run ResolutionParser to obtain resolution features and search queries.",
            "Freeze evidence time before search.",
            "Run baseline and optional routed evidence providers.",
            "Deduplicate evidence and attach source metadata.",
            "Run LLMForecaster using p_m and frozen evidence.",
            "Persist DuckDB rows and this workflow record copy.",
        ],
    }

    basename = _record_basename(run_id, candidate.market.question, created_at)
    json_path = output_dir / f"{basename}.json"
    md_path = output_dir / f"{basename}.md"

    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_markdown_record(record), encoding="utf-8")
    return json_path


def _workflow_records_dir(records_dir: str | Path | None = None) -> Path:
    root = records_dir or os.getenv("WORKFLOW_RECORDS_DIR") or "workflow_records"
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _query_summary(evidence: list[EvidenceItem]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[EvidenceItem]] = defaultdict(list)
    for item in evidence:
        grouped[(item.query, item.provider, item.source_type)].append(item)
    summary = []
    for (query, provider, source_type), items in grouped.items():
        summary.append({
            "query": query,
            "provider": provider,
            "source_type": source_type,
            "result_count": len(items),
            "max_relevance_score": max((item.relevance_score for item in items), default=0.0),
            "avg_reliability_prior": _avg(item.reliability_prior for item in items),
            "avg_resolution_relevance": _avg(item.resolution_relevance for item in items),
        })
    return sorted(summary, key=lambda item: (item["query"], item["source_type"]))


def _avg(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _record_basename(run_id: str, question: str, created_at: datetime) -> str:
    created = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slug(question)[:72] or "workflow"
    return f"{created}_{run_id[:8]}_{slug}"


def _slug(value: str) -> str:
    lowered = value.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "workflow"


def _markdown_record(record: dict[str, Any]) -> str:
    run = record["forecast_run"]
    market = record["market"]
    features = record["resolution_features"]
    evidence = record["evidence"]
    lines = [
        "# BeatOdds Workflow Record",
        "",
        f"- run_id: `{record['run_id']}`",
        f"- condition_id: `{market.get('condition_id', '')}`",
        f"- saved_at: `{record['saved_at']}`",
        f"- signal_type: `{record['signal_type']}`",
        f"- model: `{run.get('model_version', '')}`",
        "",
        "## Market",
        "",
        f"- question: {market.get('question', '')}",
        f"- slug: `{market.get('slug', '')}`",
        f"- close_time: `{market.get('close_time', '')}`",
        f"- p_m: `{run['p_m']:.4f}`",
        f"- p_f: `{run['p_f']:.4f}`",
        f"- edge: `{run['edge']:+.4f}`",
        f"- confidence: `{run['confidence']:.3f}`",
        "",
        "## Resolution Parser",
        "",
        f"- condition_type: `{features.get('condition_type', '')}`",
        f"- event_type: `{features.get('event_type', '')}`",
        f"- china_relevance: `{features.get('china_relevance', '')}`",
        f"- geography: `{', '.join(features.get('geography', []))}`",
        f"- resolution_source_hint: {features.get('resolution_source_hint', '')}",
        f"- ambiguity_score: `{features.get('ambiguity_score', 0.0)}`",
        "",
        "## Forecast Reasoning",
        "",
        run.get("reasoning", ""),
        "",
        "## Query Summary",
        "",
        "| query | provider | source_type | n | max_score | "
        "avg_reliability | avg_resolution_relevance |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in record["query_summary"]:
        lines.append(
            "| "
            f"{_cell(item['query'])} | {_cell(item['provider'])} | {_cell(item['source_type'])} | "
            f"{item['result_count']} | {item['max_relevance_score']:.3f} | "
            f"{item['avg_reliability_prior']:.3f} | "
            f"{item['avg_resolution_relevance']:.3f} |"
        )

    lines.extend([
        "",
        "## Evidence",
        "",
        "| # | score | provider | type | source | title | url |",
        "|---:|---:|---|---|---|---|---|",
    ])
    for idx, item in enumerate(evidence, start=1):
        lines.append(
            "| "
            f"{idx} | {float(item.get('relevance_score') or 0.0):.3f} | "
            f"{_cell(item.get('provider', ''))} | {_cell(item.get('source_type', ''))} | "
            f"{_cell(item.get('source', ''))} | {_cell(item.get('title', ''))} | "
            f"{_cell(item.get('url', ''))} |"
        )

    lines.extend([
        "",
        "## Reconstruction Steps",
        "",
    ])
    for idx, step in enumerate(record["reconstruction_steps"], start=1):
        lines.append(f"{idx}. {step}")
    lines.append("")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:240]
