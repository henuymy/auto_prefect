from __future__ import annotations

from datetime import date

import pytest

from services.dashboard_v2_target_service import (
    AmbiguousTargetPlanError,
    TargetPlanCandidate,
    choose_target_plan_candidate,
    normalize_target_scenario,
)


def candidate(
    plan_id: int,
    *,
    priority: int,
    effective_from: date,
) -> TargetPlanCandidate:
    return TargetPlanCandidate(
        id=plan_id,
        scenario="NORMAL",
        period_type="DAY",
        effective_from=effective_from,
        priority=priority,
    )


def test_target_plan_uses_priority_before_effective_date():
    winner = choose_target_plan_candidate(
        [
            candidate(1, priority=20, effective_from=date(2026, 1, 1)),
            candidate(2, priority=10, effective_from=date(2026, 6, 1)),
        ]
    )

    assert winner is not None
    assert winner.id == 1


def test_target_plan_uses_latest_effective_date_for_equal_priority():
    winner = choose_target_plan_candidate(
        [
            candidate(1, priority=10, effective_from=date(2026, 1, 1)),
            candidate(2, priority=10, effective_from=date(2026, 6, 1)),
        ]
    )

    assert winner is not None
    assert winner.id == 2


def test_target_plan_rejects_unresolved_tie():
    with pytest.raises(AmbiguousTargetPlanError):
        choose_target_plan_candidate(
            [
                candidate(1, priority=10, effective_from=date(2026, 1, 1)),
                candidate(2, priority=10, effective_from=date(2026, 1, 1)),
            ]
        )


def test_target_plan_returns_none_for_no_candidate():
    assert choose_target_plan_candidate([]) is None


def test_target_scenario_normalization_rejects_unknown_value():
    assert normalize_target_scenario("pk") == "PK"
    assert normalize_target_scenario(" NORMAL ") == "NORMAL"
    with pytest.raises(ValueError, match="NORMAL/PK"):
        normalize_target_scenario("OTHER")
