from __future__ import annotations

from datetime import datetime

from infrastructure.dashboard_v2_run_store import serialize_v2_collection_run
from models.dashboard_v2 import CollectionRunV2


def test_serialize_v2_collection_run_uses_node_count_and_structured_error():
    record = CollectionRunV2(
        id=1,
        batch_no="dashboard-v2-test",
        run_type="REALTIME",
        trigger_type="MANUAL",
        status="FAILED",
        phase="VALIDATE_AREA",
        started_at=datetime(2026, 6, 30, 12, 0),
        finished_at=datetime(2026, 6, 30, 12, 1),
        request_count=700,
        node_count=4717,
        row_count=4717,
        current_upsert_count=0,
        snapshot_insert_count=0,
        acc_upsert_count=0,
        error_type="NODE_COVERAGE_MISMATCH",
        error_message={"message": "coverage mismatch"},
    )

    result = serialize_v2_collection_run(record)

    assert result["node_count"] == 4717
    assert result["error_message"] == {"message": "coverage mismatch"}
    assert result["started_at"] == "2026-06-30T12:00:00.000"
