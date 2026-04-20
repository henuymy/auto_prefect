"""Report method configuration model."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MethodConfig:
    name: str
    params: dict[str, Any] = field(default_factory=dict)

