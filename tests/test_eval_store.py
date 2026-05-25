from datetime import datetime, timezone

from beatodds.common import config as config_module
from beatodds.common.types import EvalRecord
from beatodds.evaluation.store import (
    edge_distribution_summary,
    load_eval_records,
    mark_resolved,
    save_eval_records,
)


def test_eval_store_roundtrip_and_resolution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config_module._settings = None

    record = EvalRecord(
        condition_id="0xstoretest",
        snapshot_time=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
        p_m=0.4,
        p_f=0.55,
        evidence_frozen_at=datetime(2026, 5, 24, 11, 59, tzinfo=timezone.utc),
        signal_type="evidence",
        model_version="test-model",
    )

    assert save_eval_records([record]) == 1

    loaded = load_eval_records()
    assert len(loaded) == 1
    assert loaded[0].condition_id == record.condition_id
    assert edge_distribution_summary(loaded)["mean_edge"] == 0.15

    assert mark_resolved(record.condition_id, 1.0) == 1
    resolved = load_eval_records(resolved_only=True)
    assert len(resolved) == 1
    assert resolved[0].resolved_outcome == 1.0

    config_module._settings = None
