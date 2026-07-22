from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from services import dashboard_v2_orchestrator as orchestrator
from services.dashboard_structure import (
    StructureEdge,
    StructureGraph,
    StructureNode,
    merge_subtree_retry_collection,
)
from services.dashboard_v2_orchestrator import (
    V2MetricCoverageError,
    augment_v2_observed_with_base,
    validate_v2_metric_coverage,
)


def node(node_type: str, code: str, name: str | None = None, node_id: int | None = None):
    return StructureNode(
        node_type=node_type,
        code=code,
        name=name or code,
        node_id=node_id,
    )


def base_graph() -> StructureGraph:
    nodes = {
        ("CITY", "A"): node("CITY", "A", node_id=1),
        ("BRANCH", "B"): node("BRANCH", "B", node_id=2),
        ("GRID", "G"): node("GRID", "G", node_id=3),
    }
    return StructureGraph(
        areas=dict(nodes),
        targets=dict(nodes),
        edges={
            StructureEdge("CITY", "A", "BRANCH", "B"),
            StructureEdge("BRANCH", "B", "GRID", "G"),
        },
    )


def mysql_operational_error(code: int) -> OperationalError:
    return OperationalError(
        "statement",
        {},
        Exception(code, "mysql://account:secret@host/database"),
    )


class CapturingLogger:
    def __init__(self) -> None:
        self.infos: list[tuple[object, ...]] = []
        self.warnings: list[tuple[object, ...]] = []

    def info(self, message: object, *args: object) -> None:
        self.infos.append((message, *args))

    def warning(self, message: object, *args: object) -> None:
        self.warnings.append((message, *args))


@pytest.mark.parametrize(
    ("error_code", "category"),
    [(2006, "MYSQL_CONNECTION_LOST"), (2013, "MYSQL_READ_TIMEOUT")],
)
def test_v2_structure_read_retries_transient_mysql_error_with_fresh_pool(
    monkeypatch,
    error_code,
    category,
):
    graph = base_graph()
    attempts = 0
    sleep_delays: list[float] = []
    logger = CapturingLogger()
    engine = SimpleNamespace(dispose_calls=0)

    def dispose() -> None:
        engine.dispose_calls += 1

    def load_once(_engine):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise mysql_operational_error(error_code)
        return graph

    engine.dispose = dispose
    monkeypatch.setattr(orchestrator, "_load_v2_structure_graph_once", load_once)
    monkeypatch.setattr(orchestrator, "sleep", sleep_delays.append)
    clock = iter([100.0, 100.25])
    monkeypatch.setattr(orchestrator, "perf_counter", lambda: next(clock))

    result = orchestrator._load_v2_structure_graph_with_retry(
        engine,
        batch_no="v2-read-retry",
        logger=logger,
    )

    assert result is graph
    assert attempts == 2
    assert engine.dispose_calls == 1
    assert sleep_delays == [0.5]
    assert logger.warnings == [
        (
            "V2 MySQL 结构读取重试 batch_no=%s operation=%s category=%s "
            "code=%s retry_attempt=%s outcome=%s delay_seconds=%.1f",
            "v2-read-retry",
            "LOAD_STRUCTURE_GRAPH",
            category,
            error_code,
            1,
            "RETRY",
            0.5,
        )
    ]
    assert logger.infos == [
        (
            "V2 MySQL 结构读取恢复 batch_no=%s operation=%s category=%s "
            "code=%s retry_attempt=%s outcome=%s elapsed_seconds=%.3f",
            "v2-read-retry",
            "LOAD_STRUCTURE_GRAPH",
            category,
            error_code,
            1,
            "RECOVERED",
            0.25,
        )
    ]
    assert "secret" not in str(logger.warnings)
    assert "secret" not in str(logger.infos)


def test_v2_structure_read_does_not_retry_non_connection_error(monkeypatch):
    engine = SimpleNamespace(dispose_calls=0)
    logger = CapturingLogger()

    def dispose() -> None:
        engine.dispose_calls += 1

    def load_once(_engine):
        raise mysql_operational_error(1045)

    engine.dispose = dispose
    monkeypatch.setattr(orchestrator, "_load_v2_structure_graph_once", load_once)

    with pytest.raises(OperationalError):
        orchestrator._load_v2_structure_graph_with_retry(
            engine,
            batch_no="v2-read-no-retry",
            logger=logger,
        )

    assert engine.dispose_calls == 0
    assert logger.warnings == []


def test_v2_structure_read_stops_after_one_retry(monkeypatch):
    attempts = 0
    engine = SimpleNamespace(dispose_calls=0)
    logger = CapturingLogger()

    def dispose() -> None:
        engine.dispose_calls += 1

    def load_once(_engine):
        nonlocal attempts
        attempts += 1
        raise mysql_operational_error(2013)

    engine.dispose = dispose
    monkeypatch.setattr(orchestrator, "_load_v2_structure_graph_once", load_once)
    monkeypatch.setattr(orchestrator, "sleep", lambda _delay: None)
    clock = iter([100.0, 100.5])
    monkeypatch.setattr(orchestrator, "perf_counter", lambda: next(clock))

    with pytest.raises(OperationalError):
        orchestrator._load_v2_structure_graph_with_retry(
            engine,
            batch_no="v2-read-exhausted",
            logger=logger,
        )

    assert attempts == 2
    assert engine.dispose_calls == 1
    assert len(logger.warnings) == 2
    assert logger.warnings[-1][0] == (
        "V2 MySQL 结构读取失败 batch_no=%s operation=%s category=%s "
        "code=%s retry_attempt=%s outcome=%s elapsed_seconds=%.3f"
    )
    assert logger.warnings[-1][-2:] == ("FAILED", 0.5)


def test_augment_observed_preserves_approved_base_edges():
    graph = augment_v2_observed_with_base(
        base_graph(),
        [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M",
                "area_name": "经理",
                "metric": 10,
            },
            {
                "level_type": "CHANNEL",
                "area_code": "C",
                "area_name": "渠道",
                "parent_request_code": "M",
                "metric": 5,
            },
        ],
        [
            {
                "parent_type": "GRID",
                "parent_code": "G",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M",
                "child_name": "经理",
            },
            {
                "parent_type": "CHANNEL_MANAGER",
                "parent_code": "M",
                "child_type": "CHANNEL",
                "child_code": "C",
                "child_name": "渠道",
            },
        ],
    )

    assert set(graph.areas) == {
        ("CITY", "A"),
        ("BRANCH", "B"),
        ("GRID", "G"),
        ("CHANNEL_MANAGER", "M"),
        ("CHANNEL", "C"),
    }
    assert StructureEdge("CITY", "A", "BRANCH", "B") in graph.edges
    assert StructureEdge("GRID", "G", "CHANNEL_MANAGER", "M") in graph.edges


def test_metric_coverage_requires_manager_self_row():
    graph = augment_v2_observed_with_base(
        base_graph(),
        [],
        [
            {
                "parent_type": "GRID",
                "parent_code": "G",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M",
                "child_name": "经理",
            }
        ],
    )

    with pytest.raises(V2MetricCoverageError, match="missing"):
        validate_v2_metric_coverage(
            [
                {
                    "level_type": "CITY",
                    "area_code": "A",
                    "metric": 1,
                },
                {
                    "level_type": "BRANCH",
                    "area_code": "B",
                    "metric": 1,
                },
                {
                    "level_type": "GRID",
                    "area_code": "G",
                    "metric": 1,
                },
            ],
            graph,
            ["metric"],
        )


def test_metric_coverage_attaches_existing_node_ids():
    graph = base_graph()
    rows = [
        {"level_type": "CITY", "area_code": "A", "metric": "1.5"},
        {"level_type": "BRANCH", "area_code": "B", "metric": 2},
        {"level_type": "GRID", "area_code": "G", "metric": 3},
    ]

    result = validate_v2_metric_coverage(rows, graph, ["metric"])

    assert [row["node_id"] for row in result] == [2, 1, 3]
    assert Decimal(str(result[0]["metric"])) == Decimal("2")


def test_subtree_merge_replaces_manager_self_row_instead_of_duplicating_it():
    base = {
        "rows": [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M",
                "area_name": "经理",
                "metric": 1,
            }
        ],
        "structure_observations": [
            {
                "parent_type": "GRID",
                "parent_code": "G",
                "child_type": "CHANNEL_MANAGER",
                "child_code": "M",
                "child_name": "经理",
            }
        ],
        "request_count": 1,
        "recoverable_errors": [],
    }
    retry = {
        "rows": [
            {
                "level_type": "CHANNEL_MANAGER",
                "area_code": "M",
                "area_name": "经理",
                "metric": 2,
            }
        ],
        "structure_observations": base["structure_observations"],
        "request_count": 2,
        "recoverable_errors": [],
    }

    merged = merge_subtree_retry_collection(base, retry, {"G"}, {"M"})

    assert len(merged["rows"]) == 1
    assert merged["rows"][0]["metric"] == 2
