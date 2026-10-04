"""Adapter Pattern base for government source connectors.

Real authorized APIs can replace the mock implementations later
without rewriting business logic: implement SourceAdapter for the
real endpoint and register it in portal_client/adapters.py.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class SourceResult:
    status: str  # VALID, EXPIRED, MISMATCH, NOT_FOUND, UNAVAILABLE, ERROR, BLACKLIST
    data: dict = field(default_factory=dict)
    message: str = ""
    retrieved_at: Optional[datetime] = None
    latency_ms: int = 0
    source: str = ""


@dataclass
class SourceAdapter:
    source: str      # logical name, e.g. "UDYAM"
    path: str        # mock portal path, e.g. "udyam"
    identifier_field: str = "pan"  # which bidder identifier to send
