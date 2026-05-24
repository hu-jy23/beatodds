"""All pydantic v2 models shared across modules. No raw dicts cross module boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

class MarketMeta(BaseModel):
    condition_id: str
    question: str
    description: str = ""
    resolution_text: str = ""
    category: str = ""
    neg_risk: bool = False
    neg_risk_market_id: str | None = None
    token_yes_id: str = ""
    token_no_id: str = ""
    outcome_count: int = 2
    outcomes: list[str] = Field(default_factory=list)
    close_time: datetime | None = None
    created_time: datetime | None = None
    volume_24h: float = 0.0
    liquidity: float = 0.0
    active: bool = True
    slug: str = ""
    event_id: str = ""      # Gamma event id (for fetching full neg_risk group)


class PriceSnapshot(BaseModel):
    condition_id: str
    token_id: str
    snapshot_time: datetime
    midpoint: float          # (best_bid + best_ask) / 2 using CLOB v2 correct ordering
    best_bid: float
    best_ask: float
    spread: float
    last_trade_price: float | None = None
    volume_24h: float = 0.0
    source: Literal["scheduled", "triggered"] = "scheduled"


class PriceHistoryPoint(BaseModel):
    condition_id: str
    token_id: str
    ts: datetime
    price: float


class Resolution(BaseModel):
    condition_id: str
    outcome: str       # 'YES' | 'NO' | outcome string
    resolved_at: datetime
    resolution_src: Literal["api", "blockchain"] = "api"


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class CandidateMarket(BaseModel):
    market: MarketMeta
    snapshot: PriceSnapshot
    scan_flags: list[str] = Field(default_factory=list)
    priority_score: float = 0.0


# ---------------------------------------------------------------------------
# Relation Miner
# ---------------------------------------------------------------------------

class RelationEdge(BaseModel):
    source_id: str
    target_id: str
    relation: Literal["complement", "implication", "mutual_exclusive", "event_group", "neg_risk"]
    weight: float = 1.0


class ConsistencyViolation(BaseModel):
    market_ids: list[str]
    violation_type: str          # 'bundle_long' | 'bundle_short' | 'price_sum_deviation'
    expected: float
    actual: float
    edge_estimate: float         # gross edge before fees
    net_edge: float              # after taker fees (1.5%) + gas
    explanation: str = ""


class RelationGraph(BaseModel):
    nodes: list[str]
    edges: list[RelationEdge]
    violations: list[ConsistencyViolation]


# ---------------------------------------------------------------------------
# Resolution Parser
# ---------------------------------------------------------------------------

class ResolutionFeatures(BaseModel):
    condition_id: str
    condition_type: str = "unknown"   # 'price_threshold', 'event_occurrence', 'date_range', ...
    key_entities: list[str] = Field(default_factory=list)   # names, tickers, places
    search_queries: list[str] = Field(default_factory=list) # queries for Tavily
    has_explicit_deadline: bool = False
    deadline_date: datetime | None = None
    oracle_type: Literal["UMA", "admin", "external_feed", "unknown"] = "unknown"
    exception_clauses: list[str] = Field(default_factory=list)
    ambiguity_score: float = 0.0    # 0=clear, 1=highly ambiguous
    risk_flags: list[str] = Field(default_factory=list)
    parsed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    title: str
    summary: str
    url: str
    source: str
    published_at: datetime
    relevance_score: float = 0.0


class ForecastResult(BaseModel):
    condition_id: str
    p_f: float                       # fair probability estimate
    confidence: float
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    reasoning: str = ""
    frozen_at: datetime              # evidence cutoff — must be < snapshot_time
    model: str = ""


# ---------------------------------------------------------------------------
# Calibrator / Ranker
# ---------------------------------------------------------------------------

class EdgeScore(BaseModel):
    condition_id: str
    scored_at: datetime
    p_m: float                       # market price (prior)
    p_f: float                       # fair probability
    edge: float                      # p_f - p_m
    spread: float = 0.0
    fee_bps: float = 150.0           # Polymarket taker fee 1.5%
    net_edge: float = 0.0            # edge - spread/2 - fee
    signal_type: Literal["structural", "semantic", "evidence", "ensemble"]
    explanation: str = ""
    confidence: float = 0.0
    priority: int = 0


class RankedOpportunity(BaseModel):
    edge_score: EdgeScore
    market: MarketMeta
    snapshot: PriceSnapshot
    resolution_features: ResolutionFeatures | None = None
    forecast: ForecastResult | None = None
    structural_violations: list[ConsistencyViolation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class EvalRecord(BaseModel):
    """One data point in the time-consistent benchmark."""
    condition_id: str
    snapshot_time: datetime
    p_m: float
    p_f: float
    evidence_frozen_at: datetime     # must be < snapshot_time
    resolved_outcome: float | None = None   # 1.0=YES, 0.0=NO; None=unresolved
    signal_type: str = ""
    model_version: str = ""
