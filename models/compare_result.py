"""Compare result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompareResult:
    result: str
    diff_summary: dict[str, Any] = field(default_factory=dict)
    new_row_count: int = 0
    old_row_count: int = 0
    message: str = ""


@dataclass
class WorkbookCompareResult:
    result: str
    sheets: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    message: str = ""
