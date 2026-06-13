"""Core "time as currency" domain math.

Pure functions only — no Flask session, request, or DB access. This keeps the
calculations reusable across the TimeCost calculator, Couples, and Dinaro.
"""
from __future__ import annotations


# ----------------------------
# Wealth comparison data
# ----------------------------
# Net worth and annual growth in USD (approximate, Feb 2026)
BILLIONAIRES = [
    # Projected, not real (yet) — a hypothetical $1T fortune for the headline contrast.
    {"name": "The first trillionaire", "net_worth_usd": 1_000_000_000_000, "annual_growth_usd": 250_000_000_000, "projected": True},
    {"name": "Elon Musk",          "net_worth_usd": 400_000_000_000, "annual_growth_usd": 150_000_000_000},
    {"name": "Jeff Bezos",         "net_worth_usd": 240_000_000_000, "annual_growth_usd":  60_000_000_000},
    {"name": "Mark Zuckerberg",    "net_worth_usd": 210_000_000_000, "annual_growth_usd":  80_000_000_000},
    {"name": "Larry Ellison",      "net_worth_usd": 190_000_000_000, "annual_growth_usd":  50_000_000_000},
    {"name": "Bill Gates",         "net_worth_usd": 110_000_000_000, "annual_growth_usd":  10_000_000_000},
    {"name": "Donald Trump",       "net_worth_usd":   6_000_000_000, "annual_growth_usd":   1_500_000_000},
]

# Approximate conversion rates to USD
CURRENCY_TO_USD = {
    "£": 1.27,
    "$": 1.00,
    "€": 1.08,
    "¥": 0.0067,
    "₹": 0.012,
    "₩": 0.00073,
    "₽": 0.011,
}

WORKING_HOURS_PER_YEAR = 40 * 52  # 2080


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def format_wealth_time(hours: float) -> str:
    """Format a very small (or large) number of hours into a human-readable string."""
    seconds = hours * 3600
    if seconds < 1:
        # Sub-second reads as milliseconds (e.g. "3.8 milliseconds") rather than
        # the anticlimactic "0.00 seconds" — matters for the trillionaire row.
        ms = seconds * 1000
        if ms < 0.01:
            return f"{ms:.4f} milliseconds"
        return f"{ms:.2f} milliseconds" if ms < 10 else f"{ms:.0f} milliseconds"
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    if seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f} minutes"
    if hours < 24:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def wealth_comparison(item_cost: float, currency: str, user_hourly: float = 0.0) -> list[dict]:
    """Return per-billionaire time-to-afford rows for a given item cost."""
    usd_rate = CURRENCY_TO_USD.get(currency, 1.0)
    item_cost_usd = item_cost * usd_rate
    # User's time in hours (for the "can_buy" ratio)
    user_hours = (item_cost_usd / (user_hourly * usd_rate)) if user_hourly > 0 else 0.0
    rows = []
    for b in BILLIONAIRES:
        hourly_net_worth  = b["net_worth_usd"]  / WORKING_HOURS_PER_YEAR
        hourly_growth     = b["annual_growth_usd"] / WORKING_HOURS_PER_YEAR
        b_hours = item_cost_usd / hourly_growth
        can_buy_num = round(user_hours / b_hours, 1) if b_hours > 0 and user_hours > 0 else 0
        rows.append({
            "name":          b["name"],
            "projected":     b.get("projected", False),
            "by_net_worth":  format_wealth_time(item_cost_usd / hourly_net_worth),
            "by_growth":     format_wealth_time(item_cost_usd / hourly_growth),
            "can_buy":       f"{can_buy_num:,.0f}" if can_buy_num >= 1 else f"{can_buy_num:,.1f}",
            "can_buy_num":   can_buy_num,
        })
    return rows


def money_to_time(cost: float, hourly_rate: float) -> dict:
    try:
        cost = float(cost)
        hourly_rate = float(hourly_rate)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid number.", "human": ""}

    if hourly_rate <= 0:
        return {"ok": False, "error": "Hourly rate must be greater than 0.", "human": ""}

    if cost < 0:
        return {"ok": False, "error": "Cost can't be negative.", "human": ""}

    total_hours = cost / hourly_rate
    total_minutes = int(round(total_hours * 60))

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours == 0 and minutes == 0:
        human = "0m"
    elif hours == 0:
        human = f"{minutes}m"
    elif minutes == 0:
        human = f"{hours}h"
    else:
        human = f"{hours}h {minutes}m"

    return {
        "ok": True,
        "error": None,
        "cost": round(cost, 2),
        "hourly_rate": round(hourly_rate, 2),
        "total_hours": round(total_minutes / 60, 2),
        "hours": hours,
        "minutes": minutes,
        "total_minutes": total_minutes,
        "human": human,
    }


def workday_equivalent(total_hours: float, hours_per_day: float = 8.0) -> str:
    total_hours = safe_float(total_hours, 0.0)
    hours_per_day = safe_float(hours_per_day, 8.0)

    if total_hours <= 0 or hours_per_day <= 0:
        return ""

    days = total_hours / hours_per_day

    if days < 0.25:
        return "less than a quarter workday"
    if days < 1:
        return f"about {round(days, 1)} workday"
    if days < 2:
        return "about 1 workday"
    return f"about {round(days, 1)} workdays"
