"""Session- and DB-bound finance/profile helpers.

These read Flask `session` and the database, so they belong to the request
context — but they don't depend on any blueprint or the Flask app object, so
both app.py (context processor) and core.routes can import them freely without
creating a circular import.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from flask import session
from sqlalchemy import text

from database import get_db_connection as get_connection
from core.finance import safe_float

DEFAULT_CURRENCY = "£"
ALLOWED_CURRENCIES = {"$", "£", "€", "¥", "₹", "₩", "₽"}


def _currency() -> str:
    c = session.get("currency", DEFAULT_CURRENCY)
    return c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY


def _get_weekly_hours_default40() -> float:
    weekly = safe_float(_personal_value("work_hours", session.get("workHours")), 40.0)
    return weekly if weekly > 0 else 40.0


def _weekly_to_monthly_hours(weekly_hours: float) -> float:
    # Calm approximation, matches your budget logic
    return weekly_hours * 4.33


def _parse_date(val: str) -> str:
    """Return ISO date string YYYY-MM-DD (defaults to today)."""
    if not val:
        return date.today().isoformat()
    try:
        return datetime.strptime(val, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return date.today().isoformat()


def _freelance_range_to_start(range_key: str) -> str:
    """
    Returns YYYY-MM-DD lower bound for filtering.
    range_key: "month" | "7" | "30" | "90"
    """
    today = date.today()
    key = (range_key or "month").strip().lower()

    if key == "month":
        start = today.replace(day=1)
    elif key in {"7", "30", "90"}:
        start = today - timedelta(days=int(key))
    else:
        start = today.replace(day=1)

    return start.isoformat()


def _get_personal_profile() -> Optional[dict]:
    profile_id = session.get("personal_profile_id")
    if not profile_id:
        return None

    conn = get_connection()
    try:
        return conn.execute(
            text("SELECT * FROM personal_profiles WHERE id = :id"),
            {"id": profile_id},
        ).mappings().first()
    finally:
        conn.close()


def _personal_value(key: str, default=None):
    profile = _get_personal_profile()
    if profile and key in profile and profile[key] not in (None, ""):
        return profile[key]
    return session.get(key, default)


def _blank_to_none(value: str):
    v = (value or "").strip()
    return v if v else None


def _parse_optional_number(value):
    v = (value or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_effective_hourly_rate() -> Optional[float]:
    # 0) freelance override if selected
    if session.get("wageSource") == "freelance":
        fr = safe_float(session.get("freelanceHourlyRate"), 0.0)
        if fr > 0:
            return fr

    # 1) direct hourly
    hr = safe_float(_personal_value("hourly_rate", session.get("hourlyRate")), 0.0)
    if hr > 0:
        return hr

    weekly_hours = _get_weekly_hours_default40()
    hours_per_year = weekly_hours * 52.0

    # 2) annual salary
    annual = safe_float(_personal_value("annual_rate", session.get("annualRate")), 0.0)
    if annual > 0 and hours_per_year > 0:
        return annual / hours_per_year

    # 3) paycheck conversion
    paycheck = safe_float(_personal_value("paycheck_amount", session.get("paycheckAmount")), 0.0)
    freq = (_personal_value("pay_frequency", session.get("payFrequency")) or "").lower().strip()

    if paycheck > 0 and freq in {"weekly", "biweekly", "monthly"}:
        if freq == "weekly":
            annual_from_pay = paycheck * 52.0
        elif freq == "biweekly":
            annual_from_pay = paycheck * 26.0
        else:
            annual_from_pay = paycheck * 12.0

        if hours_per_year > 0:
            return annual_from_pay / hours_per_year

    return None


def _hourly_from_wage(wage_amount: float, wage_type: str) -> float:
    """
    Convert user-entered wage to hourly, using Personal workHours/week.
    """
    weekly_hours = _get_weekly_hours_default40()
    hours_per_year = weekly_hours * 52.0
    hours_per_month = hours_per_year / 12.0

    wage_type = (wage_type or "").lower().strip()

    if wage_type == "hourly":
        return wage_amount
    if wage_type == "weekly":
        return wage_amount / weekly_hours if weekly_hours > 0 else 0.0
    if wage_type == "biweekly":
        return wage_amount / (weekly_hours * 2.0) if weekly_hours > 0 else 0.0
    if wage_type == "monthly":
        return wage_amount / hours_per_month if hours_per_month > 0 else 0.0
    if wage_type == "annual":
        return wage_amount / hours_per_year if hours_per_year > 0 else 0.0

    return wage_amount


def _prefill_wage_from_personal() -> tuple[str, str, str]:
    """
    Returns (wageType, wageAmount, source)
    source is "personal" when it came from session data, else "default"
    """
    hourly_rate = str(_personal_value("hourly_rate", session.get("hourlyRate")) or "").strip()
    annual_rate = str(_personal_value("annual_rate", session.get("annualRate")) or "").strip()
    paycheck_amount = str(_personal_value("paycheck_amount", session.get("paycheckAmount")) or "").strip()
    pay_frequency = str(_personal_value("pay_frequency", session.get("payFrequency")) or "").strip().lower()

    if hourly_rate:
        return "hourly", hourly_rate, "personal"
    if annual_rate:
        return "annual", annual_rate, "personal"
    if paycheck_amount and pay_frequency in {"weekly", "biweekly", "monthly"}:
        return pay_frequency, paycheck_amount, "personal"

    return "hourly", "", "default"


def _require_user_key() -> str:
    return session["user_key"]


def _current_household_id() -> Optional[int]:
    hid = session.get("household_id")
    try:
        return int(hid) if hid is not None else None
    except (TypeError, ValueError):
        return None
