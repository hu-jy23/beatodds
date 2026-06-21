"""Stateful workflow storage for live forward evaluation.

This layer records the full lifecycle around a market: discovery, snapshots,
resolution parsing, forecast runs, evidence, and outcomes. It intentionally
coexists with the compact EvalRecord store while the workflow DB matures.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import (
    CandidateMarket,
    EvidenceItem,
    ForecastResult,
    MarketMeta,
    PriceSnapshot,
    ResolutionFeatures,
)
from beatodds.evaluation.workflow_records import save_workflow_record_copy


def _db_path() -> Path:
    cfg = get_settings()
    path = Path(cfg.data_dir) / "eval.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _json_list(values: list[str]) -> str:
    return json.dumps(values)


def _json_dict(values: dict[str, Any]) -> str:
    return json.dumps(values)


def _load_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _load_json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_time(value: datetime | None) -> datetime | None:
    normalized = _as_utc(value)
    return normalized.replace(tzinfo=None) if normalized else None


def _connect():
    import duckdb

    conn = duckdb.connect(str(_db_path()))
    ensure_schema(conn)
    return conn


def ensure_schema(conn) -> None:
    """Create workflow tables if needed."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracked_markets (
            condition_id        TEXT PRIMARY KEY,
            question            TEXT,
            description         TEXT,
            resolution_text     TEXT,
            category            TEXT,
            slug                TEXT,
            event_id            TEXT,
            neg_risk            BOOLEAN,
            neg_risk_market_id  TEXT,
            token_yes_id        TEXT,
            token_no_id         TEXT,
            outcome_count       INTEGER,
            outcomes_json       TEXT,
            close_time          TIMESTAMP,
            created_time        TIMESTAMP,
            volume_24h          DOUBLE,
            liquidity           DOUBLE,
            active              BOOLEAN,
            tracking_status     TEXT,
            first_seen_at       TIMESTAMP,
            last_seen_at        TIMESTAMP,
            resolved_outcome    DOUBLE,
            resolved_at         TIMESTAMP,
            resolution_source   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            snapshot_id     TEXT PRIMARY KEY,
            condition_id    TEXT,
            token_id        TEXT,
            snapshot_time   TIMESTAMP,
            midpoint        DOUBLE,
            best_bid        DOUBLE,
            best_ask        DOUBLE,
            spread          DOUBLE,
            volume_24h      DOUBLE,
            source          TEXT,
            priority_score  DOUBLE,
            scan_flags_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resolution_features (
            condition_id          TEXT PRIMARY KEY,
            condition_type        TEXT,
            event_type            TEXT,
            china_relevance       TEXT,
            key_entities_json     TEXT,
            search_queries_json   TEXT,
            geography_json        TEXT,
            resolution_source_hint TEXT,
            source_routing_hints_json TEXT,
            has_explicit_deadline BOOLEAN,
            deadline_date         TIMESTAMP,
            oracle_type           TEXT,
            exception_clauses_json TEXT,
            ambiguity_score       DOUBLE,
            risk_flags_json       TEXT,
            parsed_at             TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_runs (
            run_id             TEXT PRIMARY KEY,
            condition_id       TEXT,
            snapshot_time      TIMESTAMP,
            evidence_frozen_at TIMESTAMP,
            p_m                DOUBLE,
            p_f                DOUBLE,
            edge               DOUBLE,
            confidence         DOUBLE,
            signal_type        TEXT,
            model_version      TEXT,
            reasoning          TEXT,
            created_at         TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_evidence_items (
            evidence_id      TEXT PRIMARY KEY,
            run_id           TEXT,
            condition_id     TEXT,
            query            TEXT,
            title            TEXT,
            summary          TEXT,
            url              TEXT,
            source           TEXT,
            published_at     TIMESTAMP,
            retrieved_at     TIMESTAMP,
            relevance_score  DOUBLE,
            provider         TEXT,
            source_type      TEXT,
            direction        TEXT,
            strength         DOUBLE,
            resolution_relevance DOUBLE,
            reliability_prior DOUBLE,
            dedupe_key       TEXT,
            raw_metadata_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outcomes (
            condition_id       TEXT PRIMARY KEY,
            resolved_outcome   DOUBLE,
            resolved_at        TIMESTAMP,
            resolution_source  TEXT,
            notes              TEXT
        )
    """)
    _ensure_columns(conn, "resolution_features", {
        "event_type": "TEXT",
        "china_relevance": "TEXT",
        "geography_json": "TEXT",
        "resolution_source_hint": "TEXT",
        "source_routing_hints_json": "TEXT",
    })
    _ensure_columns(conn, "forecast_runs", {
        "forecast_direction": "TEXT",
    })
    _ensure_columns(conn, "workflow_evidence_items", {
        "provider": "TEXT",
        "source_type": "TEXT",
        "direction": "TEXT",
        "strength": "DOUBLE",
        "resolution_relevance": "DOUBLE",
        "reliability_prior": "DOUBLE",
        "dedupe_key": "TEXT",
        "raw_metadata_json": "TEXT",
    })
    conn.commit()


def _ensure_columns(conn, table_name: str, columns: dict[str, str]) -> None:
    existing = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    }
    for name, type_sql in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {type_sql}")


def upsert_tracked_market(market: MarketMeta, seen_at: datetime | None = None) -> None:
    """Create or update long-lived market tracking state."""
    seen_at = seen_at or _now()
    conn = _connect()
    first_seen = conn.execute(
        "SELECT first_seen_at FROM tracked_markets WHERE condition_id = ?",
        [market.condition_id],
    ).fetchone()
    first_seen_at = first_seen[0] if first_seen else seen_at

    conn.execute("""
        INSERT OR REPLACE INTO tracked_markets (
            condition_id, question, description, resolution_text, category,
            slug, event_id, neg_risk, neg_risk_market_id, token_yes_id,
            token_no_id, outcome_count, outcomes_json, close_time, created_time,
            volume_24h, liquidity, active, tracking_status, first_seen_at,
            last_seen_at, resolved_outcome, resolved_at, resolution_source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        market.condition_id,
        market.question,
        market.description,
        market.resolution_text,
        market.category,
        market.slug,
        market.event_id,
        market.neg_risk,
        market.neg_risk_market_id,
        market.token_yes_id,
        market.token_no_id,
        market.outcome_count,
        _json_list(market.outcomes),
        _db_time(market.close_time),
        _db_time(market.created_time),
        market.volume_24h,
        market.liquidity,
        market.active,
        "tracking",
        _db_time(first_seen_at),
        _db_time(seen_at),
        None,
        None,
        None,
    ])
    conn.close()


def append_market_snapshot(
    snapshot: PriceSnapshot,
    priority_score: float = 0.0,
    scan_flags: list[str] | None = None,
) -> str:
    """Append one immutable market snapshot and return its snapshot id."""
    snapshot_id = (
        f"{snapshot.condition_id}:{snapshot.token_id}:"
        f"{snapshot.snapshot_time.isoformat()}"
    )
    conn = _connect()
    conn.execute("""
        INSERT OR REPLACE INTO market_snapshots (
            snapshot_id, condition_id, token_id, snapshot_time, midpoint,
            best_bid, best_ask, spread, volume_24h, source,
            priority_score, scan_flags_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        snapshot_id,
        snapshot.condition_id,
        snapshot.token_id,
        _db_time(snapshot.snapshot_time),
        snapshot.midpoint,
        snapshot.best_bid,
        snapshot.best_ask,
        snapshot.spread,
        snapshot.volume_24h,
        snapshot.source,
        priority_score,
        _json_list(scan_flags or []),
    ])
    conn.close()
    return snapshot_id


def save_candidate(candidate: CandidateMarket, seen_at: datetime | None = None) -> str:
    """Upsert market metadata and append its current snapshot."""
    upsert_tracked_market(candidate.market, seen_at=seen_at)
    return append_market_snapshot(
        candidate.snapshot,
        priority_score=candidate.priority_score,
        scan_flags=candidate.scan_flags,
    )


def save_resolution_features(features: ResolutionFeatures) -> None:
    conn = _connect()
    conn.execute("""
        INSERT OR REPLACE INTO resolution_features (
            condition_id, condition_type, event_type, china_relevance,
            key_entities_json, search_queries_json, geography_json,
            resolution_source_hint, source_routing_hints_json,
            has_explicit_deadline, deadline_date, oracle_type,
            exception_clauses_json, ambiguity_score, risk_flags_json, parsed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        features.condition_id,
        features.condition_type,
        features.event_type,
        features.china_relevance,
        _json_list(features.key_entities),
        _json_list(features.search_queries),
        _json_list(features.geography),
        features.resolution_source_hint,
        _json_list(features.source_routing_hints),
        features.has_explicit_deadline,
        _db_time(features.deadline_date),
        features.oracle_type,
        _json_list(features.exception_clauses),
        features.ambiguity_score,
        _json_list(features.risk_flags),
        _db_time(features.parsed_at),
    ])
    conn.close()


def save_forecast_run(
    candidate: CandidateMarket,
    features: ResolutionFeatures,
    evidence: list[EvidenceItem],
    forecast: ForecastResult,
    evidence_frozen_at: datetime,
    signal_type: str = "search_only_llm",
    created_at: datetime | None = None,
) -> str:
    """Persist one full forecast run with evidence provenance."""
    save_candidate(candidate)
    save_resolution_features(features)

    run_id = str(uuid4())
    p_m = candidate.snapshot.midpoint
    p_f = forecast.p_f
    created_at = created_at or _now()
    conn = _connect()
    conn.execute("""
        INSERT INTO forecast_runs (
            run_id, condition_id, snapshot_time, evidence_frozen_at,
            p_m, p_f, edge, confidence, forecast_direction, signal_type, model_version,
            reasoning, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        run_id,
        candidate.market.condition_id,
        _db_time(candidate.snapshot.snapshot_time),
        _db_time(evidence_frozen_at),
        p_m,
        p_f,
        p_f - p_m,
        forecast.confidence,
        forecast.forecast_direction,
        signal_type,
        forecast.model,
        forecast.reasoning,
        _db_time(created_at),
    ])

    fallback_query = (
        features.search_queries[0]
        if features.search_queries else candidate.market.question
    )
    retrieved_at = evidence_frozen_at
    for item in evidence:
        conn.execute("""
            INSERT INTO workflow_evidence_items (
                evidence_id, run_id, condition_id, query, title, summary, url,
                source, published_at, retrieved_at, relevance_score, provider,
                source_type, direction, strength, resolution_relevance,
                reliability_prior, dedupe_key, raw_metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            str(uuid4()),
            run_id,
            candidate.market.condition_id,
            item.query or fallback_query,
            item.title,
            item.summary,
            item.url,
            item.source,
            _db_time(item.published_at),
            _db_time(item.retrieved_at or retrieved_at),
            item.relevance_score,
            item.provider,
            item.source_type,
            item.direction,
            item.strength,
            item.resolution_relevance,
            item.reliability_prior,
            item.dedupe_key,
            _json_dict(item.raw_metadata),
        ])
    conn.close()
    try:
        save_workflow_record_copy(
            run_id=run_id,
            candidate=candidate,
            features=features,
            evidence=evidence,
            forecast=forecast,
            evidence_frozen_at=evidence_frozen_at,
            signal_type=signal_type,
            created_at=created_at,
        )
    except Exception as exc:
        logger.warning(f"WorkflowStore: workflow record copy failed for {run_id}: {exc}")
    logger.info(
        f"WorkflowStore: saved forecast run {run_id} for "
        f"{candidate.market.condition_id[:20]}"
    )
    return run_id


def mark_outcome(
    condition_id: str,
    outcome: float,
    resolved_at: datetime | None = None,
    source: str = "manual",
    notes: str = "",
) -> int:
    """Persist a resolved outcome and mark the tracked market resolved."""
    resolved_at = resolved_at or _now()
    conn = _connect()
    exists = conn.execute(
        "SELECT COUNT(*) FROM tracked_markets WHERE condition_id = ?",
        [condition_id],
    ).fetchone()[0]
    conn.execute("""
        INSERT OR REPLACE INTO outcomes (
            condition_id, resolved_outcome, resolved_at, resolution_source, notes
        )
        VALUES (?, ?, ?, ?, ?)
    """, [condition_id, outcome, _db_time(resolved_at), source, notes])
    conn.execute("""
        UPDATE tracked_markets
        SET tracking_status = 'resolved',
            resolved_outcome = ?,
            resolved_at = ?,
            resolution_source = ?
        WHERE condition_id = ?
    """, [outcome, _db_time(resolved_at), source, condition_id])
    conn.close()
    return int(exists)


def _tracked_market_row_to_dict(row) -> dict:
    return {
        "condition_id": row[0],
        "question": row[1],
        "category": row[2],
        "tracking_status": row[3],
        "first_seen_at": row[4],
        "last_seen_at": row[5],
        "resolved_outcome": row[6],
    }


def load_tracked_market(condition_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute("""
        SELECT condition_id, question, category, tracking_status,
               first_seen_at, last_seen_at, resolved_outcome
        FROM tracked_markets
        WHERE condition_id = ?
    """, [condition_id]).fetchone()
    conn.close()
    return _tracked_market_row_to_dict(row) if row else None


def load_tracked_markets(limit: int = 50) -> list[dict]:
    conn = _connect()
    rows = conn.execute("""
        SELECT condition_id, question, category, tracking_status,
               first_seen_at, last_seen_at, resolved_outcome
        FROM tracked_markets
        ORDER BY last_seen_at DESC
        LIMIT ?
    """, [limit]).fetchall()
    conn.close()
    return [_tracked_market_row_to_dict(row) for row in rows]


def load_market_snapshots(condition_id: str, limit: int = 20) -> list[dict]:
    conn = _connect()
    rows = conn.execute("""
        SELECT snapshot_id, condition_id, token_id, snapshot_time, midpoint,
               best_bid, best_ask, spread, volume_24h, source,
               priority_score, scan_flags_json
        FROM market_snapshots
        WHERE condition_id = ?
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, [condition_id, limit]).fetchall()
    conn.close()
    return [
        {
            "snapshot_id": row[0],
            "condition_id": row[1],
            "token_id": row[2],
            "snapshot_time": row[3],
            "midpoint": row[4],
            "best_bid": row[5],
            "best_ask": row[6],
            "spread": row[7],
            "volume_24h": row[8],
            "source": row[9],
            "priority_score": row[10],
            "scan_flags": _load_json_list(row[11]),
        }
        for row in rows
    ]


def load_forecast_runs(condition_id: str | None = None, limit: int = 50) -> list[dict]:
    conn = _connect()
    if condition_id:
        rows = conn.execute("""
            SELECT run_id, condition_id, snapshot_time, evidence_frozen_at,
                   p_m, p_f, edge, confidence, forecast_direction,
                   signal_type, model_version,
                   reasoning, created_at
            FROM forecast_runs
            WHERE condition_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, [condition_id, limit]).fetchall()
    else:
        rows = conn.execute("""
            SELECT run_id, condition_id, snapshot_time, evidence_frozen_at,
                   p_m, p_f, edge, confidence, forecast_direction,
                   signal_type, model_version,
                   reasoning, created_at
            FROM forecast_runs
            ORDER BY created_at DESC
            LIMIT ?
        """, [limit]).fetchall()
    conn.close()
    return [
        {
            "run_id": row[0],
            "condition_id": row[1],
            "snapshot_time": row[2],
            "evidence_frozen_at": row[3],
            "p_m": row[4],
            "p_f": row[5],
            "edge": row[6],
            "confidence": row[7],
            "forecast_direction": row[8] or "observe",
            "signal_type": row[9],
            "model_version": row[10],
            "reasoning": row[11],
            "created_at": row[12],
        }
        for row in rows
    ]


def load_due_markets(
    stale_after_hours: float = 24.0,
    limit: int = 50,
    now: datetime | None = None,
) -> list[dict]:
    """Return active tracked markets that need a new forecast run."""
    now_utc = _as_utc(now or _now())
    conn = _connect()
    rows = conn.execute("""
        WITH latest_snapshots AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY condition_id ORDER BY snapshot_time DESC
                   ) AS rn
            FROM market_snapshots
        ),
        latest_forecasts AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY condition_id ORDER BY created_at DESC
                   ) AS rn
            FROM forecast_runs
        )
        SELECT tm.condition_id, tm.question, tm.category, tm.last_seen_at,
               ls.snapshot_time, ls.midpoint, ls.spread,
               lf.created_at, lf.p_f, lf.edge, lf.model_version
        FROM tracked_markets tm
        LEFT JOIN latest_snapshots ls
            ON tm.condition_id = ls.condition_id AND ls.rn = 1
        LEFT JOIN latest_forecasts lf
            ON tm.condition_id = lf.condition_id AND lf.rn = 1
        WHERE tm.tracking_status = 'tracking'
          AND tm.resolved_outcome IS NULL
        ORDER BY tm.last_seen_at DESC
    """).fetchall()
    conn.close()

    due: list[dict] = []
    for row in rows:
        latest_forecast_at = _as_utc(row[7])
        if latest_forecast_at is None:
            hours_since_forecast = None
            due_reason = "never_forecasted"
        else:
            delta = now_utc - latest_forecast_at
            hours_since_forecast = delta.total_seconds() / 3600
            if hours_since_forecast < stale_after_hours:
                continue
            due_reason = f"stale_{hours_since_forecast:.1f}h"

        due.append({
            "condition_id": row[0],
            "question": row[1],
            "category": row[2],
            "last_seen_at": row[3],
            "latest_snapshot_time": row[4],
            "p_m": row[5],
            "spread": row[6],
            "latest_forecast_at": row[7],
            "latest_p_f": row[8],
            "latest_edge": row[9],
            "latest_model_version": row[10],
            "hours_since_forecast": hours_since_forecast,
            "due_reason": due_reason,
        })

    due.sort(key=lambda item: (
        item["latest_forecast_at"] is not None,
        -(item["hours_since_forecast"] if item["hours_since_forecast"] else 1e9),
    ))
    return due[:limit]


def load_evidence_for_run(run_id: str) -> list[EvidenceItem]:
    conn = _connect()
    rows = conn.execute("""
        SELECT query, title, summary, url, source, published_at, relevance_score,
               retrieved_at, provider, source_type, direction, strength,
               resolution_relevance, reliability_prior, dedupe_key, raw_metadata_json
        FROM workflow_evidence_items
        WHERE run_id = ?
        ORDER BY relevance_score DESC NULLS LAST
    """, [run_id]).fetchall()
    conn.close()
    return [
        EvidenceItem(
            query=row[0],
            title=row[1],
            summary=row[2],
            url=row[3],
            source=row[4],
            published_at=row[5],
            relevance_score=row[6],
            retrieved_at=row[7],
            provider=row[8] or "tavily",
            source_type=row[9] or "western_source",
            direction=row[10] or "neutral",
            strength=row[11] or 0.0,
            resolution_relevance=row[12] or 0.0,
            reliability_prior=row[13] or 0.0,
            dedupe_key=row[14] or "",
            raw_metadata=_load_json_dict(row[15]),
        )
        for row in rows
    ]


def workflow_summary() -> dict:
    """Return compact counts for CLI status output and smoke tests."""
    conn = _connect()
    tracked = conn.execute("SELECT COUNT(*) FROM tracked_markets").fetchone()[0]
    snapshots = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    forecasts = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
    evidence = conn.execute("SELECT COUNT(*) FROM workflow_evidence_items").fetchone()[0]
    outcomes = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    conn.close()
    return {
        "tracked_markets": tracked,
        "market_snapshots": snapshots,
        "forecast_runs": forecasts,
        "evidence_items": evidence,
        "outcomes": outcomes,
    }


def load_resolution_features(condition_id: str) -> ResolutionFeatures | None:
    conn = _connect()
    row = conn.execute("""
        SELECT condition_id, condition_type, key_entities_json, search_queries_json,
               has_explicit_deadline, deadline_date, oracle_type,
               exception_clauses_json, ambiguity_score, risk_flags_json, parsed_at,
               event_type, china_relevance, geography_json, resolution_source_hint,
               source_routing_hints_json
        FROM resolution_features
        WHERE condition_id = ?
    """, [condition_id]).fetchone()
    conn.close()
    if row is None:
        return None
    return ResolutionFeatures(
        condition_id=row[0],
        condition_type=row[1],
        key_entities=_load_json_list(row[2]),
        search_queries=_load_json_list(row[3]),
        has_explicit_deadline=bool(row[4]),
        deadline_date=row[5],
        oracle_type=row[6],
        exception_clauses=_load_json_list(row[7]),
        ambiguity_score=row[8],
        risk_flags=_load_json_list(row[9]),
        parsed_at=row[10],
        event_type=row[11] or "other",
        china_relevance=row[12] or "low",
        geography=_load_json_list(row[13]),
        resolution_source_hint=row[14] or "",
        source_routing_hints=_load_json_list(row[15]),
    )
