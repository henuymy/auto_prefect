"""Task configuration model."""

from dataclasses import dataclass


@dataclass
class TaskConfig:
    name: str
    enabled: bool = True

