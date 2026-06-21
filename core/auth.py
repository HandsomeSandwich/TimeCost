"""Salted PIN hashing/verification shared by Personal profiles and Dinaro.

These are generic auth utilities, not Dinaro-specific - keep them here so
modules don't have to import each other's private helpers.
"""
from __future__ import annotations

import hashlib
import secrets


def pin_hash(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()


def make_pin(pin: str) -> tuple[str, str]:
    salt = secrets.token_hex(8)
    return pin_hash(pin, salt), salt


def verify_pin(pin: str, stored_hash: str, salt: str) -> bool:
    return pin_hash(pin, salt) == stored_hash
