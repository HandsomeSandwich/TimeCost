from __future__ import annotations

import os
from typing import Optional

from flask import Flask, render_template, request, session, redirect, url_for
from sqlalchemy import text

from database import engine, get_db_connection as get_connection, init_db

DEFAULT_CURRENCY = "£"
ALLOWED_CURRENCIES = {"$", "£", "€", "¥", "₹", "₩", "₽"}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

# --- PiggyBank ---
from PiggyBank import piggybank_bp
import PiggyBank.routes  # ensures routes are registered
app.register_blueprint(piggybank_bp, url_prefix="/piggybank")

# Initialize DB on startup
try:
    init_db()
except Exception as e:
    print("Database init error:", e)


@app.context_processor
def inject_globals():
    return {
        "currency": session.get("currency", DEFAULT_CURRENCY),
        "perspective": session.get("perspective", "river"),
        "is_parent": session.get("piggy_parent", False),
        "guide": session.get("guide", "lorelai"),
    }


# ----------------------------
# Helpers
# ----------------------------

def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _currency() -> str:
    c = session.get("currency", DEFAULT_CURRENCY)
    return c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY


def _get_weekly_hours_default40() -> float:
    weekly = safe_float(session.get("workHours"), 40.0)
    return weekly if weekly > 0 else 40.0


def _weekly_to_monthly_hours(weekly_hours: float) -> float:
    # Calm approximation (matches your budget logic)
    return weekly_hours * 4.33


# Keep your existing helpers exactly as you already have them:
# - money_to_time
# - workday_equivalent
# - week_equivalent
# - get_effective_hourly_rate
# - _hourly_from_wage
# - _prefill_wage_from_personal
# etc.


# ----------------------------
# Routes
# ----------------------------

@app.route("/", methods=["GET", "POST"])
def calculator():
    # keep your existing calculator route body unchanged
    # just make sure any currency passed into templates uses _currency() if you pass it explicitly
    return render_template("calculator.html", currency=_currency())


@app.route("/personal", methods=["GET", "POST"])
def personal():
    if request.method == "POST":
        session["username"] = (request.form.get("username") or "").strip()

        # normalize workHours into a numeric-ish value stored as string or float
        # (Flask session serializes cleanly either way)
        work_hours = safe_float(request.form.get("workHours"), 40.0)
        session["workHours"] = work_hours

        c = request.form.get("currency", session.get("currency", DEFAULT_CURRENCY))
        session["currency"] = c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY

        annual_rate = request.form.get("annualRate")
        hourly_rate = request.form.get("hourlyRate")
        pay_frequency = request.form.get("payFrequency")
        paycheck_amount = request.form.get("paycheckAmount")

        if annual_rate is not None:
            session["annualRate"] = annual_rate
        if hourly_rate is not None:
            session["hourlyRate"] = hourly_rate
        if pay_frequency is not None:
            session["payFrequency"] = pay_frequency
        if paycheck_amount is not None:
            session["paycheckAmount"] = paycheck_amount

        return redirect(url_for("calculator"))

    # Pull expenses total for display (read-only)
    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses")
        ).mappings().first()
        expenses_total = float(row["total"]) if row and row["total"] is not None else 0.0
    except Exception:
        expenses_total = 0.0
    finally:
        conn.close()

    return render_template(
        "personal.html",
        username=session.get("username", ""),
        annualRate=session.get("annualRate", ""),
        hourlyRate=session.get("hourlyRate", ""),
        workHours=session.get("workHours", 40),
        expenses=expenses_total,
        paycheckAmount=session.get("paycheckAmount", ""),
        payFrequency=session.get("payFrequency", ""),
        currency=_currency(),
    )


@app.route("/timebank", methods=["GET", "POST"])
def timebank():
    currency = _currency()

    def fetch_savings_total() -> float:
        conn = get_connection()
        try:
            row = conn.execute(
                text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE category = 'Savings'")
            ).mappings().first()
            return float(row["total"]) if row and row["total"] is not None else 0.0
        finally:
            conn.close()

    def fetch_all_expenses():
        conn = get_connection()
        try:
            return conn.execute(text("SELECT amount, category FROM expenses")).mappings().all()
        finally:
            conn.close()

    if request.method == "POST":
        income = safe_float(request.form.get("income"), 0.0)
        expenses = safe_float(request.form.get("expenses"), 0.0)
        hoursWorked = safe_float(request.form.get("hoursWorked"), 0.0)

        savings_value = fetch_savings_total()

        return render_template(
            "timebank.html",
            income=income,
            expenses=expenses,
            hoursWorked=hoursWorked,
            savings_value=savings_value,
            currency=currency,
        )

    # GET defaults
    income = safe_float(session.get("annualRate"), 0.0) / 12.0

    all_rows = fetch_all_expenses()
    expenses_total = sum((row.get("amount") or 0) for row in all_rows)
    savings_value = sum((row.get("amount") or 0) for row in all_rows if row.get("category") == "Savings")

    weekly_hours = safe_float(session.get("workHours"), 0.0)
    hoursWorked = _weekly_to_monthly_hours(weekly_hours) if weekly_hours > 0 else 0.0

    return render_template(
        "timebank.html",
        income=income,
        expenses=expenses_total,
        hoursWorked=hoursWorked,
        savings_value=savings_value,
        currency=currency,
    )


@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    # keep your existing expenses logic unchanged
    # just ensure render_template uses currency=_currency()
    return render_template("expenses.html", currency=_currency())


@app.route("/budget", methods=["GET", "POST"])
def budget():
    currency = _currency()
    # keep existing budget logic, but pass currency=currency
    return render_template("budget.html", currency=currency)


@app.route("/goals", methods=["GET", "POST"])
def goals():
    # keep existing goals logic, but pass currency=_currency()
    return render_template("goals.html", currency=_currency())


@app.route("/staples", methods=["GET"])
def staples():
    currency = _currency()
    hr = session.get("hourlyRate", "")

    if not hr:
        eff = get_effective_hourly_rate()  # keep your existing function
        hr = f"{eff:.2f}" if eff and eff > 0 else ""

    return render_template("staples.html", hourlyRate=hr, currency=currency)


@app.route("/set_currency", methods=["POST"])
def set_currency():
    c = request.form.get("currency", DEFAULT_CURRENCY)
    session["currency"] = c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY
    return redirect(request.referrer or url_for("calculator"))


@app.route("/set_perspective", methods=["POST"])
def set_perspective():
    allowed = {"river", "leslie", "eddie"}
    p = request.form.get("perspective", "river")
    session["perspective"] = p if p in allowed else "river"
    return redirect(request.referrer or url_for("calculator"))
