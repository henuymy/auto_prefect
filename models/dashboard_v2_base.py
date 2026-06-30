"""Declarative base for the greenfield Dashboard V2 schema."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from models.dashboard_base import NAMING_CONVENTION


class DashboardV2Base(DeclarativeBase):
    """Keep V2 metadata isolated from the legacy dashboard schema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
