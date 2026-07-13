"""Database-agnostic protocol for Dashboard collection run persistence."""

from __future__ import annotations

from typing import Any, Protocol


class CollectionRunStore(Protocol):
    def create(
        self,
        batch_no: str,
        trigger_type: str,
        run_type: str = "REALTIME",
    ) -> dict[str, Any]: ...

    def update(self, batch_no: str, **changes: Any) -> dict[str, Any]: ...

    def get(self, batch_no: str) -> dict[str, Any]: ...

    def latest(self) -> dict[str, Any] | None: ...

    def close(self) -> None: ...
