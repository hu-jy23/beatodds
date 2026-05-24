"""DuckDB connection management and schema initialization."""

from __future__ import annotations

from pathlib import Path

import duckdb
from loguru import logger

from beatodds.common.config import get_settings

_DB_PATH = "beatodds.duckdb"


def get_db(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    settings = get_settings()
    path = db_path or (settings.data_dir / _DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


DDL = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id        VARCHAR PRIMARY KEY,
    question            VARCHAR,
    description         VARCHAR,
    resolution_text     VARCHAR,
    category            VARCHAR,
    neg_risk            BOOLEAN DEFAULT FALSE,
    neg_risk_market_id  VARCHAR,
    token_yes_id        VARCHAR,
    token_no_id         VARCHAR,
    outcome_count       INTEGER DEFAULT 2,
    outcomes_json       VARCHAR,
    close_time          TIMESTAMPTZ,
    created_time        TIMESTAMPTZ,
    volume_24h          DOUBLE DEFAULT 0,
    liquidity           DOUBLE DEFAULT 0,
    active              BOOLEAN DEFAULT TRUE,
    slug                VARCHAR,
    fetched_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id              VARCHAR,
    condition_id    VARCHAR,
    token_id        VARCHAR,
    snapshot_time   TIMESTAMPTZ,
    midpoint        DOUBLE,
    best_bid        DOUBLE,
    best_ask        DOUBLE,
    spread          DOUBLE,
    volume_24h      DOUBLE DEFAULT 0,
    source          VARCHAR DEFAULT 'scheduled'
);

CREATE TABLE IF NOT EXISTS price_history (
    condition_id    VARCHAR,
    token_id        VARCHAR,
    ts              TIMESTAMPTZ,
    price           DOUBLE
);

CREATE TABLE IF NOT EXISTS resolutions (
    condition_id    VARCHAR PRIMARY KEY,
    outcome         VARCHAR,
    resolved_at     TIMESTAMPTZ,
    resolution_src  VARCHAR DEFAULT 'api'
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id              VARCHAR,
    condition_id    VARCHAR,
    title           VARCHAR,
    summary         VARCHAR,
    url             VARCHAR,
    source          VARCHAR,
    published_at    TIMESTAMPTZ,
    frozen_at       TIMESTAMPTZ,
    relevance_score DOUBLE DEFAULT 0
);

CREATE TABLE IF NOT EXISTS edge_scores (
    id              VARCHAR,
    condition_id    VARCHAR,
    scored_at       TIMESTAMPTZ,
    p_m             DOUBLE,
    p_f             DOUBLE,
    edge            DOUBLE,
    spread          DOUBLE DEFAULT 0,
    fee_bps         DOUBLE DEFAULT 150,
    net_edge        DOUBLE,
    signal_type     VARCHAR,
    explanation     VARCHAR,
    confidence      DOUBLE DEFAULT 0,
    priority        INTEGER DEFAULT 0
);
"""


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    for stmt in DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    logger.info("DuckDB schema initialized")


def ensure_schema() -> duckdb.DuckDBPyConnection:
    conn = get_db()
    init_schema(conn)
    return conn
