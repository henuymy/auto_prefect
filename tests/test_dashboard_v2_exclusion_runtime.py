from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.dashboard_v2 import (
    ChannelIndicatorExclusion,
    IndicatorV2,
    MetricCaliberOverride,
)
from services.dashboard_v2_exclusion_runtime import (
    write_channel_indicator_exclusion_overrides_in_session,
)
from tests.test_dashboard_v2_query_service import _engine


BUSINESS_DATE = date(2026, 6, 30)
COLLECTED_AT = datetime(2026, 6, 30, 10, 10)


def _rows() -> list[dict[str, object]]:
    return [
        {"node_id": 1, "channel_count": Decimal("200")},
        {"node_id": 2, "channel_count": Decimal("100")},
        {"node_id": 3, "channel_count": Decimal("50")},
        {"node_id": 4, "channel_count": Decimal("20")},
        {"node_id": 5, "channel_count": Decimal("12")},
    ]


def _rows_with_derived() -> list[dict[str, object]]:
    return [
        {
            "node_id": row["node_id"],
            "channel_count": row["channel_count"],
            "derived_count": Decimal(str(row["channel_count"])) * Decimal("2.5"),
        }
        for row in _rows()
    ]


def test_exclusion_runtime_marks_channel_and_deducts_all_ancestors():
    engine = _engine()
    with Session(engine) as session:
        rule = ChannelIndicatorExclusion(
            channel_node_id=5,
            indicator_id=1,
            effective_from=BUSINESS_DATE,
            status="ACTIVE",
        )
        session.add(rule)
        session.flush()

        stats = write_channel_indicator_exclusion_overrides_in_session(
            session,
            collection_run_id=2,
            business_date=BUSINESS_DATE,
            collected_at=COLLECTED_AT,
            rows=_rows(),
            store_codes=["channel_count"],
            custom_components={},
        )
        session.flush()
        overrides = {
            row.node_id: row
            for row in session.scalars(
                select(MetricCaliberOverride).order_by(MetricCaliberOverride.node_id)
            )
        }

        assert stats["rule_count"] == 1
        assert stats["excluded_cell_count"] == 1
        assert stats["adjusted_cell_count"] == 4
        assert stats["override_count"] == 5
        assert len(str(stats["rule_fingerprint"])) == 64
        assert overrides[5].metric_value is None
        assert overrides[5].value_state == "EXCLUDED"
        assert overrides[4].metric_value == Decimal("8.0000")
        assert overrides[3].metric_value == Decimal("38.0000")
        assert overrides[2].metric_value == Decimal("88.0000")
        assert overrides[1].metric_value == Decimal("188.0000")


def test_exclusion_runtime_clears_previous_run_overrides_when_rule_is_inactive():
    engine = _engine()
    with Session(engine) as session:
        rule = ChannelIndicatorExclusion(
            channel_node_id=5,
            indicator_id=1,
            effective_from=BUSINESS_DATE,
            status="ACTIVE",
        )
        session.add(rule)
        session.flush()
        write_channel_indicator_exclusion_overrides_in_session(
            session,
            collection_run_id=2,
            business_date=BUSINESS_DATE,
            collected_at=COLLECTED_AT,
            rows=_rows(),
            store_codes=["channel_count"],
            custom_components={},
        )
        session.flush()
        assert session.scalar(select(MetricCaliberOverride.id).limit(1)) is not None

        rule.status = "CANCELLED"
        stats = write_channel_indicator_exclusion_overrides_in_session(
            session,
            collection_run_id=2,
            business_date=BUSINESS_DATE,
            collected_at=COLLECTED_AT,
            rows=_rows(),
            store_codes=["channel_count"],
            custom_components={},
        )
        session.flush()

        assert stats["override_count"] == 0
        assert list(session.scalars(select(MetricCaliberOverride))) == []


def test_direct_custom_exclusion_prevents_duplicate_ancestor_deduction():
    engine = _engine()
    with Session(engine) as session:
        session.add(
            IndicatorV2(
                id=2,
                code="derived_count",
                name="派生渠道数",
                indicator_type="CUSTOM",
                storage_mode="STORE",
                enabled=True,
                source_active=True,
                sort_order=2,
            )
        )
        session.add_all(
            [
                ChannelIndicatorExclusion(
                    channel_node_id=5,
                    indicator_id=1,
                    effective_from=BUSINESS_DATE,
                    status="ACTIVE",
                ),
                ChannelIndicatorExclusion(
                    channel_node_id=5,
                    indicator_id=2,
                    effective_from=BUSINESS_DATE,
                    status="ACTIVE",
                ),
            ]
        )
        session.flush()

        write_channel_indicator_exclusion_overrides_in_session(
            session,
            collection_run_id=2,
            business_date=BUSINESS_DATE,
            collected_at=COLLECTED_AT,
            rows=_rows_with_derived(),
            store_codes=["channel_count", "derived_count"],
            custom_components={
                "derived_count": [
                    {"source_code": "channel_count", "coefficient": Decimal("2.5")}
                ]
            },
        )
        session.flush()
        derived_overrides = {
            row.node_id: row
            for row in session.scalars(
                select(MetricCaliberOverride).where(
                    MetricCaliberOverride.indicator_id == 2
                )
            )
        }

        assert derived_overrides[5].value_state == "EXCLUDED"
        assert derived_overrides[4].metric_value == Decimal("20.0000")
        assert derived_overrides[3].metric_value == Decimal("95.0000")
        assert derived_overrides[2].metric_value == Decimal("220.0000")
        assert derived_overrides[1].metric_value == Decimal("470.0000")
