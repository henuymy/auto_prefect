"""Sanitized, reusable health metadata for the shared Session snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


SESSION_HEALTH_METADATA_FIELDS = {
    "healthy",
    "verified_at",
    "cookie_hash",
    "healthy_stages",
    "failure_classification",
}


def cookie_snapshot_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as cookie_file:
        for chunk in iter(lambda: cookie_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_session_health(path: Path) -> dict:
    try:
        with Path(path).open("r", encoding="utf-8") as state_file:
            payload = json.load(state_file)
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_session_health_atomic(path: Path, payload: dict) -> Path:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{uuid4().hex}.tmp")
    sanitized_payload = {
        key: value for key, value in payload.items() if key in SESSION_HEALTH_METADATA_FIELDS
    }
    try:
        with temporary.open("w", encoding="utf-8") as state_file:
            json.dump(sanitized_payload, state_file, ensure_ascii=False, indent=2)
        os.replace(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)
    return resolved


def session_health_is_fresh(
    state: dict,
    *,
    cookie_hash: str,
    required_stages: list[str],
    freshness_seconds: int,
    now: datetime | None = None,
) -> bool:
    if not state.get("healthy") or state.get("cookie_hash") != cookie_hash:
        return False
    if not set(required_stages).issubset(set(state.get("healthy_stages") or [])):
        return False
    try:
        verified_at = datetime.fromisoformat(state["verified_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc).astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_seconds = (current - verified_at).total_seconds()
    return 0 <= age_seconds <= freshness_seconds
