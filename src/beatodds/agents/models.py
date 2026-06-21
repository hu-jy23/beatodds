"""Shared models for the agentic China forecast harness."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from beatodds.evidence.providers.base import SearchResult


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str, fallback: str = "item", max_len: int = 96) -> str:
    lowered = (value or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return (slug or fallback)[:max_len].strip("_") or fallback


def new_run_id(agent_name: str, created_at: datetime | None = None) -> str:
    created = created_at or utc_now()
    prefix = slugify(agent_name, fallback="agent", max_len=32)
    return f"{prefix}_{created.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


def agent_workspace_name(agent_name: str = "", agent_model: str = "") -> str:
    """Human-readable agent directory name for md-first workspaces."""
    raw = (agent_name or agent_model or "agent").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    lowered = raw.lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip("-._")
    return name or "agent"


class AgentRunContext(BaseModel):
    """Input package for one event / market / agent run."""

    event_title: str
    market_question: str
    condition_id: str = ""
    event_id: str = ""
    event_slug: str = ""
    market_slug: str = ""
    resolution_text: str = ""
    p_m: float | None = None
    deadline: datetime | None = None
    agent_name: str = "gpt-5.4-mini"
    agent_run_id: str = ""
    agent_model: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    harness_doc_path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("p_m")
    @classmethod
    def _validate_probability(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not 0.0 <= value <= 1.0:
            raise ValueError("p_m must be in [0, 1]")
        return value

    @property
    def normalized_event_slug(self) -> str:
        return slugify(self.event_slug or self.event_title, fallback="event")

    @property
    def normalized_market_slug(self) -> str:
        return slugify(self.market_slug or self.market_question, fallback="market")

    @property
    def normalized_agent_run_id(self) -> str:
        return self.agent_run_id or agent_workspace_name(self.agent_name, self.agent_model)


class AgentToolSpec(BaseModel):
    name: str
    description: str
    source_categories: list[str] = Field(default_factory=list)
    available: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentToolResult(BaseModel):
    action_id: str = Field(default_factory=lambda: uuid4().hex)
    tool_name: str
    source_category: str
    query: str = ""
    status: Literal["ok", "error", "skipped"] = "ok"
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    results: list[SearchResult] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    tool_name: str
    query: str = ""
    url: str = ""
    source_category: str = "generic_search_tools"
    args: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    loop_index: int
    phase: Literal["understand", "plan", "tool", "analyze", "review", "report"]
    summary: str
    action: str = ""
    analysis: str = ""
    next_decision: str = ""
    tool_call: ToolCallRequest | None = None
    report: "ForecastReportDraft | None" = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrajectoryStep(BaseModel):
    step_id: str = Field(default_factory=lambda: uuid4().hex)
    loop_index: int = 0
    phase: Literal["understand", "plan", "tool", "analyze", "review", "report"] = "tool"
    summary: str
    action: str = ""
    observation: str = ""
    analysis: str = ""
    next_decision: str = ""
    tool_name: str = ""
    tool_action_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCard(BaseModel):
    card_id: str = Field(default_factory=lambda: uuid4().hex)
    title: str
    url: str
    source: str = ""
    source_category: str
    provider: str = ""
    query: str = ""
    summary: str = ""
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    relevance_score: float = 0.0
    reliability_prior: float = 0.0
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class ForecastReportDraft(BaseModel):
    condition_id: str
    p_m: float | None = None
    p_f: float | None = None
    confidence: float | None = None
    p_m_delta: float | None = None
    calibration_status: str = "not_calibrated"
    report_markdown: str
    created_at: datetime = Field(default_factory=utc_now)
    model: str = ""
    evidence_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ForecastOutcomeProbability(BaseModel):
    """One outcome row for multi-outcome prediction markets."""

    outcome: str
    p_f: float | None = None
    p_m: float | None = None
    p_m_delta: float | None = None
    confidence: float | None = None
    rationale: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("p_f", "p_m", "confidence")
    @classmethod
    def _validate_optional_probability(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not 0.0 <= value <= 1.0:
            raise ValueError("probability fields must be in [0, 1]")
        return value


class MultiOutcomeForecast(BaseModel):
    """Machine-readable schema for mutually exclusive or grouped outcomes."""

    condition_id: str = ""
    event_id: str = ""
    market_question: str = ""
    outcomes: list[ForecastOutcomeProbability]
    top_outcome: str = ""
    calibration_status: str = "uncalibrated"
    model: str = ""
    evidence_paths: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def p_f_total(self) -> float:
        return sum(item.p_f or 0.0 for item in self.outcomes)
