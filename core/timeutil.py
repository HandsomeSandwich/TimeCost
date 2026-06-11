"""Timestamp helpers shared across modules."""
from __future__ import annotations

from datetime import datetime


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, second precision."""
    return datetime.utcnow().isoformat(timespec="seconds")
