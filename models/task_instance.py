"""Task instance model."""

from dataclasses import dataclass


@dataclass
class TaskInstance:
    instance_id: str
    task_name: str
    status: str = "pending"

