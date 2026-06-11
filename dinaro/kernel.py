"""Small, pure helpers Dinaro depends on.

Vendored here (a deliberate copy of the app's core helpers) so the dinaro
package has no import dependency on the parent application and can be lifted
into its own repository. These are tiny, stable functions — the duplication is
cheaper than coupling Dinaro to the main app's core package.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def pin_hash(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()


def make_pin(pin: str) -> tuple[str, str]:
    salt = secrets.token_hex(8)
    return pin_hash(pin, salt), salt


def verify_pin(pin: str, stored_hash: str, salt: str) -> bool:
    return pin_hash(pin, salt) == stored_hash


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, second precision."""
    return datetime.utcnow().isoformat(timespec="seconds")
