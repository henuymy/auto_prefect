"""Notification payload model."""

from dataclasses import dataclass, field


@dataclass
class NotifyPayload:
    image_paths: list[str] = field(default_factory=list)
    text_content: str = ""

