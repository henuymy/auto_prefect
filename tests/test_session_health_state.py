import json
from datetime import datetime

from services.session_health_state import (
    cookie_snapshot_hash,
    read_session_health,
    session_health_is_fresh,
    write_session_health_atomic,
)


def test_cookie_snapshot_hash_tracks_file_contents(tmp_path):
    cookie_path = tmp_path / "cookie.json"
    cookie_path.write_text('{"Cookie": "first"}', encoding="utf-8")
    first_hash = cookie_snapshot_hash(cookie_path)

    cookie_path.write_text('{"Cookie": "second"}', encoding="utf-8")

    assert cookie_snapshot_hash(cookie_path) != first_hash


def test_read_session_health_treats_missing_or_invalid_state_as_empty(tmp_path):
    state_path = tmp_path / "session_state.json"

    assert read_session_health(state_path) == {}

    state_path.write_text("not-json", encoding="utf-8")
    assert read_session_health(state_path) == {}


def test_write_session_health_atomic_publishes_payload(tmp_path):
    state_path = tmp_path / "session_state.json"
    payload = {"healthy": True, "cookie_hash": "abc"}

    resolved = write_session_health_atomic(state_path, payload)

    assert resolved == state_path.resolve()
    assert json.loads(state_path.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob(f".{state_path.name}.*.tmp")) == []


def test_write_session_health_atomic_drops_non_metadata_fields(tmp_path):
    state_path = tmp_path / "session_state.json"

    write_session_health_atomic(
        state_path,
        {
            "healthy": False,
            "failure_classification": "authentication",
            "Cookie": "sentinel-cookie-secret",
        },
    )

    serialized = state_path.read_text(encoding="utf-8")
    assert json.loads(serialized) == {
        "healthy": False,
        "failure_classification": "authentication",
    }
    assert "sentinel-cookie-secret" not in serialized


def test_fresh_health_state_with_matching_cookie_hash_is_reusable():
    state = {
        "healthy": True,
        "verified_at": "2026-07-12T20:00:00+08:00",
        "cookie_hash": "abc",
        "healthy_stages": ["report_analysis", "smart_ops", "city_ops"],
    }

    assert session_health_is_fresh(
        state,
        cookie_hash="abc",
        required_stages=["report_analysis", "smart_ops"],
        freshness_seconds=180,
        now=datetime.fromisoformat("2026-07-12T20:02:59+08:00"),
    ) is True


def test_health_state_is_stale_on_age_hash_or_stage_mismatch():
    base = {
        "healthy": True,
        "verified_at": "2026-07-12T20:00:00+08:00",
        "cookie_hash": "abc",
        "healthy_stages": ["report_analysis"],
    }

    assert session_health_is_fresh(
        base,
        cookie_hash="abc",
        required_stages=["report_analysis"],
        freshness_seconds=180,
        now=datetime.fromisoformat("2026-07-12T20:03:01+08:00"),
    ) is False
    assert session_health_is_fresh(
        base,
        cookie_hash="different",
        required_stages=["report_analysis"],
        freshness_seconds=180,
    ) is False
    assert session_health_is_fresh(
        base,
        cookie_hash="abc",
        required_stages=["report_analysis", "city_ops"],
        freshness_seconds=180,
    ) is False
