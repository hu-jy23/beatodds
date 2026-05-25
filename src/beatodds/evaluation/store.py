"""DuckDB-backed storage for EvalRecords.

Persists each forecast run so we can compute Brier Skill Score once markets resolve.
Schema: one row per (condition_id, snapshot_time, signal_type) combination.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import EvalRecord


def _db_path() -> Path:
    cfg = get_settings()
    p = Path(cfg.data_dir) / "eval.duckdb"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_records (
            condition_id       TEXT,
            snapshot_time      TIMESTAMP,
            evidence_frozen_at TIMESTAMP,
            p_m                DOUBLE,
            p_f                DOUBLE,
            signal_type        TEXT,
            model_version      TEXT,
            resolved_outcome   DOUBLE,
            recorded_at        TIMESTAMP DEFAULT now(),
            PRIMARY KEY (condition_id, snapshot_time, signal_type)
        )
    """)


def save_eval_records(records: list[EvalRecord]) -> int:
    """Upsert EvalRecords to DuckDB. Returns number of rows written."""
    import duckdb
    conn = duckdb.connect(str(_db_path()))
    _ensure_table(conn)

    written = 0
    for r in records:
        conn.execute("""
            INSERT OR REPLACE INTO eval_records
                (condition_id, snapshot_time, evidence_frozen_at, p_m, p_f,
                 signal_type, model_version, resolved_outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            r.condition_id,
            r.snapshot_time,
            r.evidence_frozen_at,
            r.p_m,
            r.p_f,
            r.signal_type,
            r.model_version,
            r.resolved_outcome,
        ])
        written += 1

    conn.close()
    logger.info(f"EvalStore: wrote {written} records to {_db_path()}")
    return written


def load_eval_records(resolved_only: bool = False) -> list[EvalRecord]:
    """Load all EvalRecords from DuckDB."""
    import duckdb
    conn = duckdb.connect(str(_db_path()))
    _ensure_table(conn)

    where = "WHERE resolved_outcome IS NOT NULL" if resolved_only else ""
    rows = conn.execute(f"""
        SELECT condition_id, snapshot_time, evidence_frozen_at,
               p_m, p_f, signal_type, model_version, resolved_outcome
        FROM eval_records {where}
        ORDER BY snapshot_time DESC
    """).fetchall()
    conn.close()

    return [
        EvalRecord(
            condition_id=row[0],
            snapshot_time=row[1],
            evidence_frozen_at=row[2],
            p_m=row[3],
            p_f=row[4],
            signal_type=row[5],
            model_version=row[6],
            resolved_outcome=row[7],
        )
        for row in rows
    ]


def mark_resolved(condition_id: str, outcome: float) -> int:
    """Set resolved_outcome for all records matching condition_id. Returns rows updated."""
    import duckdb
    conn = duckdb.connect(str(_db_path()))
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT COUNT(*) FROM eval_records WHERE condition_id = ?",
        [condition_id],
    ).fetchone()[0]
    conn.execute(
        "UPDATE eval_records SET resolved_outcome = ? WHERE condition_id = ?",
        [outcome, condition_id],
    )
    conn.close()
    logger.info(f"mark_resolved: {rows} row(s) updated for {condition_id[:20]} → {outcome}")
    return rows


def edge_distribution_summary(records: list[EvalRecord]) -> dict:
    """Quick stats on edge distribution (no resolution needed)."""
    if not records:
        return {}
    edges = [r.p_f - r.p_m for r in records]
    abs_edges = [abs(e) for e in edges]
    n = len(edges)
    return {
        "n": n,
        "mean_edge": round(sum(edges) / n, 4),
        "mean_abs_edge": round(sum(abs_edges) / n, 4),
        "max_edge": round(max(edges), 4),
        "min_edge": round(min(edges), 4),
        "pct_edge_gt_3pct": round(sum(1 for e in abs_edges if e > 0.03) / n, 3),
    }
