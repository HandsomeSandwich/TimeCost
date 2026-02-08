from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from itertools import zip_longest
from typing import Optional
from collections import defaultdict
import secrets

from flask import Flask, render_template, request, session, redirect, url_for
from sqlalchemy import text

from database import engine, get_db_connection as get_connection, init_db

DEFAULT_CURRENCY = "£"
ALLOWED_CURRENCIES = {"$", "£", "€", "¥", "₹", "₩", "₽"}

# ----------------------------
# App setup
# ----------------------------
app = Flask(__name__)
# In production, set FLASK_SECRET_KEY in env so sessions persist across restarts.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

# Favicon
@app.get("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.svg"))

# --- PiggyBank ---
from PiggyBank import piggybank_bp
import PiggyBank.routes
app.register_blueprint(piggybank_bp, url_prefix="/piggybank")

# Initialize DB on startup
try:
    init_db()
except Exception as e:
    print("Database init error:", e)


# ----------------------------
# View mode: Dawn / Dusk
# ----------------------------
@app.post("/set-view")
def set_view():
    view = request.form.get("view")
    if view in ("dawn", "dusk"):
        session["view"] = view
    return redirect(request.referrer or url_for("calculator"))


@app.before_request
def ensure_view():
    if "view" not in session:
        session["view"] = "dawn"


@app.before_request
def ensure_identity():
    if "user_key" not in session:
        session["user_key"] = secrets.token_urlsafe(16)



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

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def equal_hours_including_personal(
    personal_a: float,
    personal_b: float,
    shared_total: float,
    rate_a: float,
    rate_b: float,
) -> dict:
    personal_a = safe_float(personal_a, 0.0)
    personal_b = safe_float(personal_b, 0.0)
    shared_total = safe_float(shared_total, 0.0)
    rate_a = safe_float(rate_a, 0.0)
    rate_b = safe_float(rate_b, 0.0)

    if shared_total < 0 or rate_a <= 0 or rate_b <= 0:
        return {"ok": False}

    # x = how much of SHARED Partner A pays
    x = (rate_a * (personal_b + shared_total) - rate_b * personal_a) / (rate_a + rate_b)
    a_shared = clamp(x, 0.0, shared_total)
    b_shared = shared_total - a_shared

    hours_a = (personal_a + a_shared) / rate_a if rate_a > 0 else 0.0
    hours_b = (personal_b + b_shared) / rate_b if rate_b > 0 else 0.0

    return {
        "ok": True,
        "a_shared": round(a_shared, 2),
        "b_shared": round(b_shared, 2),
        "hours_a": round(hours_a, 2),
        "hours_b": round(hours_b, 2),
        "target_hours": round((hours_a + hours_b) / 2.0, 2),
        "clamped": not (0.0 < x < shared_total),
    }



def get_effective_hourly_rate() -> Optional[float]:
    # 0) freelance override if selected
    if session.get("wageSource") == "freelance":
        fr = safe_float(session.get("freelanceHourlyRate"), 0.0)
        if fr > 0:
            return fr

    # 1) direct hourly
    hr = safe_float(session.get("hourlyRate"), 0.0)
    if hr > 0:
        return hr

    weekly_hours = _get_weekly_hours_default40()
    hours_per_year = weekly_hours * 52.0

    # 2) annual salary
    annual = safe_float(session.get("annualRate"), 0.0)
    if annual > 0 and hours_per_year > 0:
        return annual / hours_per_year

    # 3) paycheck conversion
    paycheck = safe_float(session.get("paycheckAmount"), 0.0)
    freq = (session.get("payFrequency") or "").lower().strip()

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
    hourly_rate = (session.get("hourlyRate") or "").strip()
    annual_rate = (session.get("annualRate") or "").strip()
    paycheck_amount = (session.get("paycheckAmount") or "").strip()
    pay_frequency = (session.get("payFrequency") or "").strip().lower()

    if hourly_rate:
        return "hourly", hourly_rate, "personal"
    if annual_rate:
        return "annual", annual_rate, "personal"
    if paycheck_amount and pay_frequency in {"weekly", "biweekly", "monthly"}:
        return pay_frequency, paycheck_amount, "personal"

    return "hourly", "", "default"


# ----------------------------
# Routes: Calculator
# ----------------------------
@app.route("/", methods=["GET", "POST"])
def calculator():
    pre_wage_type, pre_wage_amount, _source = _prefill_wage_from_personal()

    result = None
    item_name = ""
    item_cost = ""
    time_cost = None
    workday_text = ""

    display_wage_type = pre_wage_type
    display_wage_amount = pre_wage_amount
    prefill_source = "personal" if (pre_wage_amount or "").strip() else None

    if request.method == "POST":
        item_name = request.form.get("itemName", "")
        item_cost = (request.form.get("itemCost") or "").strip()

        wage_type = (request.form.get("wageType") or pre_wage_type).strip().lower()
        wage_amount_raw = (request.form.get("wageAmount") or "").strip()

        display_wage_type = wage_type
        display_wage_amount = wage_amount_raw if wage_amount_raw != "" else pre_wage_amount
        prefill_source = "personal" if wage_amount_raw == "" and (pre_wage_amount or "").strip() else None

        try:
            item_cost_f = float(item_cost)
            if item_cost_f < 0:
                raise ValueError("Cost can't be negative")

            if wage_amount_raw == "":
                effective_hourly = get_effective_hourly_rate()
                if effective_hourly is None or effective_hourly <= 0:
                    raise ValueError("No wage available")
                hourly_rate = float(effective_hourly)
            else:
                wage_amount_f = float(wage_amount_raw)
                if wage_amount_f <= 0:
                    raise ValueError("Wage must be > 0")

                hourly_rate = _hourly_from_wage(wage_amount_f, wage_type)
                if hourly_rate <= 0:
                    raise ValueError("Converted wage must be > 0")

            result = item_cost_f / hourly_rate
            time_cost = money_to_time(item_cost_f, hourly_rate)

            if time_cost["ok"]:
                workday_text = workday_equivalent(time_cost["total_hours"])

        except (ValueError, TypeError, ZeroDivisionError):
            result = "Invalid input"
            time_cost = {"ok": False, "error": "Invalid input.", "human": ""}

    return render_template(
        "calculator.html",
        result=result,
        item_name=item_name,
        item_cost=item_cost,
        pre_wage_type=pre_wage_type,
        pre_wage_amount=pre_wage_amount,
        prefill_source=prefill_source,
        time_cost=time_cost,
        workday_text=workday_text,
        display_wage_type=display_wage_type,
        display_wage_amount=display_wage_amount,
    )


# ----------------------------
# Routes: Personal
# ----------------------------
@app.route("/personal", methods=["GET", "POST"])
def personal():
    if request.method == "POST":
        session["username"] = (request.form.get("username") or "").strip()
        session["workHours"] = safe_float(request.form.get("workHours"), 40.0)

        c = request.form.get("currency", session.get("currency", DEFAULT_CURRENCY))
        session["currency"] = c if c in ALLOWED_CURRENCIES else DEFAULT_CURRENCY

        session["annualRate"] = (request.form.get("annualRate") or "").strip()
        session["hourlyRate"] = (request.form.get("hourlyRate") or "").strip()
        session["payFrequency"] = (request.form.get("payFrequency") or "").strip().lower()
        session["paycheckAmount"] = (request.form.get("paycheckAmount") or "").strip()

        return redirect(url_for("calculator"))

    conn = get_connection()
    try:
        row = conn.execute(text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses")).mappings().first()
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


# ----------------------------
# Routes: Timebank
# ----------------------------
@app.route("/timebank", methods=["GET", "POST"])
def timebank():
    currency = _currency()

    def fetch_savings_total() -> float:
        conn = get_connection()
        try:
            row = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE category = 'Nest Egg'")
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

    income = safe_float(session.get("annualRate"), 0.0) / 12.0

    all_rows = fetch_all_expenses()
    expenses_total = sum((row.get("amount") or 0) for row in all_rows)
    savings_value = sum((row.get("amount") or 0) for row in all_rows if row.get("category") in ("Nest Egg", "Savings")
)

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


# ----------------------------
# Routes: Expenses
# ----------------------------
@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    if request.method == "POST":
        # If user clicked "Add expense", insert a blank row and bounce back
        if "add" in request.form:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO expenses (name, amount, category, scope) "
                        "VALUES (:name, :amount, :category, :scope)"
                    ),
                    {"name": "", "amount": 0.0, "category": "House & Light", "scope": "personal"},
                )
            return redirect(url_for("expenses"))

        # Otherwise treat as Save
        expense_names = request.form.getlist("expense_name[]")
        expense_amounts = request.form.getlist("expense_amount[]")
        expense_categories = request.form.getlist("expense_category[]")
        expense_scopes = request.form.getlist("expense_scope[]")

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM expenses"))

            for name, amount, category, scope in zip_longest(
                expense_names, expense_amounts, expense_categories, expense_scopes
            ):
                name = (name or "").strip()
                category = (category or "").strip()
                scope = (scope or "personal").strip() or "personal"

                try:
                    amt = float(amount)
                except (TypeError, ValueError):
                    continue

                # keep blank rows from being saved forever
                if not name:
                    continue

                conn.execute(
                    text(
                        "INSERT INTO expenses (name, amount, category, scope) "
                        "VALUES (:name, :amount, :category, :scope)"
                    ),
                    {"name": name, "amount": amt, "category": category, "scope": scope},
                )

        return redirect(url_for("expenses"))

    # GET
    conn = get_connection()
    try:
        saved_expenses = conn.execute(text("SELECT * FROM expenses ORDER BY id ASC")).mappings().all()
        category_totals = conn.execute(
            text("SELECT category, COALESCE(SUM(amount), 0) AS total FROM expenses GROUP BY category")
        ).mappings().all()
        hourly_value = get_effective_hourly_rate() or 0.0
    finally:
        conn.close()

    return render_template(
        "expenses.html",
        saved_expenses=saved_expenses,
        category_totals=category_totals,
        currency=_currency(),
        hourly_value=hourly_value,
    )


@app.post("/expenses/reset")
def expenses_reset():
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM expenses"))
    return redirect(url_for("expenses"))

@app.route("/update_expense_category", methods=["POST"])
def update_expense_category():
    expense_id = request.form.get("expense_id")
    new_category = request.form.get("new_category")
    if not expense_id or not new_category:
        return redirect(url_for("expenses"))

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE expenses SET category = :cat WHERE id = :id"),
            {"cat": new_category, "id": int(expense_id)},
        )

    return redirect(url_for("expenses"))


@app.route("/remove_expense/<int:index>", methods=["POST"])
def remove_expense(index):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM expenses WHERE id = :id"), {"id": index})
    return redirect(url_for("expenses"))

@app.route("/couple", methods=["GET", "POST"])
def couple():
    """
    Equal-hours split (including personal expenses).
    Partner A = you (this session)
    Partner B = your partner (entered manually for now)
    """

    # ---- 1) Pull expenses from DB
    conn = get_connection()
    try:
        rows = conn.execute(
            text("SELECT amount, category FROM expenses")
        ).mappings().all()
    finally:
        conn.close()

    total = sum(safe_float(r.get("amount"), 0.0) for r in rows)

    # ---- 2) Define what counts as "shared"
    # For now: House & Light is shared (mortgage/utilities).
    # You can expand later with a "shared" checkbox column.
    shared_categories = {"House & Light"}
    shared_total = sum(
        safe_float(r.get("amount"), 0.0)
        for r in rows
        if (r.get("category") or "") in shared_categories
    )

    # Everything else is personal (including Nest Egg, Provisions, Odds)
    personal_total = max(0.0, total - shared_total)

    # ---- 3) Partner hourly rates
    # A comes from your Personal page/session
    rate_a = get_effective_hourly_rate() or 0.0

    # B entered via form (for now)
    rate_b = safe_float(request.form.get("partner_hourly"), 0.0) if request.method == "POST" else 0.0

    # ---- 4) Personal split assumption (temporary)
    # Until we add per-row "owner", we assume personal_total is split by who paid it.
    # For now: let user enter their own personal spend share %.
    a_personal = safe_float(request.form.get("my_personal"), 0.0) if request.method == "POST" else 0.0
    b_personal = max(0.0, personal_total - a_personal)

    result = None
    if request.method == "POST" and rate_a > 0 and rate_b > 0:
        result = equal_hours_including_personal(
            personal_a=a_personal,
            personal_b=b_personal,
            shared_total=shared_total,
            rate_a=rate_a,
            rate_b=rate_b,
        )

    return render_template(
        "couple.html",
        currency=_currency(),
        total=round(total, 2),
        shared_total=round(shared_total, 2),
        personal_total=round(personal_total, 2),
        rate_a=rate_a,
        rate_b=rate_b,
        a_personal=round(a_personal, 2),
        b_personal=round(b_personal, 2),
        result=result,
    )

# ----------------------------
# Routes: Budget
# ----------------------------
@app.route("/budget", methods=["GET", "POST"])
def budget():
    currency = _currency()

    conn = get_connection()
    try:
        row = conn.execute(text("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses")).mappings().first()
        expenses_total = float(row["total"]) if row and row["total"] is not None else 0.0
    except Exception:
        expenses_total = 0.0
    finally:
        conn.close()

    def fetch_goals():
        conn2 = get_connection()
        try:
            return conn2.execute(text("SELECT * FROM goals")).mappings().all()
        except Exception:
            return []
        finally:
            conn2.close()

    if request.method == "POST":
        income_input = request.form.get("income")
        hours_input = request.form.get("weeklyHours")

        income = safe_float(income_input, safe_float(session.get("annualRate"), 0.0) / 12.0)
        weekly_hours = safe_float(hours_input, safe_float(session.get("workHours"), 0.0))
        monthly_hours = weekly_hours * 4.33 if weekly_hours > 0 else 0.0

        discretionary_income = income - expenses_total
        hourly_value = (discretionary_income / monthly_hours) if monthly_hours > 0 else 0.0

        savings_goal = safe_float(request.form.get("savingsGoal"), 0.0)
        current_savings = safe_float(request.form.get("currentSavings"), 0.0)

        remaining_to_save = (savings_goal - current_savings) if savings_goal > 0 else 0.0
        progress_percent = (current_savings / savings_goal) * 100.0 if savings_goal > 0 else 0.0

        goals_rows = fetch_goals()

        return render_template(
            "budget.html",
            income=income,
            expenses=expenses_total,
            weekly_hours=weekly_hours,
            monthly_hours=monthly_hours,
            discretionary_income=discretionary_income,
            hourly_value=hourly_value,
            savings_goal=savings_goal,
            current_savings=current_savings,
            remaining_to_save=remaining_to_save,
            progress_percent=progress_percent,
            goals=goals_rows,
            currency=currency,
        )

    income = safe_float(session.get("annualRate"), 0.0) / 12.0
    weekly_hours = safe_float(session.get("workHours"), 0.0)
    monthly_hours = weekly_hours * 4.33 if weekly_hours > 0 else 0.0
    discretionary_income = income - expenses_total
    hourly_value = (discretionary_income / monthly_hours) if monthly_hours > 0 else 0.0

    goals_rows = fetch_goals()

    return render_template(
        "budget.html",
        income=income,
        expenses=expenses_total,
        weekly_hours=weekly_hours,
        monthly_hours=monthly_hours,
        discretionary_income=discretionary_income,
        hourly_value=hourly_value,
        savings_goal=0,
        current_savings=0,
        remaining_to_save=0,
        progress_percent=0,
        goals=goals_rows,
        currency=currency,
    )


# ----------------------------
# Routes: Goals
# ----------------------------
@app.route("/goals", methods=["GET", "POST"])
def goals():
    if request.method == "POST":
        with engine.begin() as conn:
            if "new_goal" in request.form:
                name = (request.form.get("goal_name") or "").strip()
                target = safe_float(request.form.get("target_amount"), 0.0)
                current = safe_float(request.form.get("current_savings"), 0.0)

                if name:
                    conn.execute(
                        text("INSERT INTO goals (name, target, current) VALUES (:n,:t,:c)"),
                        {"n": name, "t": target, "c": current},
                    )

            elif "update_goal" in request.form:
                goal_id = int(safe_float(request.form.get("goal_index"), 0))
                add_amount = safe_float(request.form.get("savings_to_add"), 0.0)

                goal_row = conn.execute(
                    text("SELECT current FROM goals WHERE id = :id"),
                    {"id": goal_id},
                ).mappings().first()

                if goal_row:
                    new_total = safe_float(goal_row.get("current"), 0.0) + add_amount
                    conn.execute(
                        text("UPDATE goals SET current = :c WHERE id = :id"),
                        {"c": new_total, "id": goal_id},
                    )

        return redirect(url_for("goals"))

    conn = get_connection()
    try:
        goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
    finally:
        conn.close()

    return render_template("goals.html", goals=goals_rows, currency=_currency())


@app.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM goals WHERE id = :id"), {"id": goal_id})
    return redirect(url_for("goals"))


# ----------------------------
# Routes: Staples
# ----------------------------
@app.route("/staples", methods=["GET"])
def staples():
    currency = _currency()
    hr = session.get("hourlyRate", "")

    if not hr:
        eff = get_effective_hourly_rate()
        hr = f"{eff:.2f}" if eff and eff > 0 else ""

    return render_template("staples.html", hourlyRate=hr, currency=currency)


# ----------------------------
# Routes: Freelance
# Table columns assumed:
# id, entry_date, client, hours, hourly_rate, notes
# ----------------------------
@app.route("/freelance", methods=["GET", "POST"])
def freelance():
    range_key = (request.args.get("range") or "month").strip().lower()
    start_date = _freelance_range_to_start(range_key)

    date_col = "entry_date"

    # POST: Use this effective rate in TimeCost
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "use_effective_rate":
            conn = get_connection()
            try:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT hours, hourly_rate
                        FROM freelance_entries
                        WHERE {date_col} >= :start
                        """
                    ),
                    {"start": start_date},
                ).mappings().all()
            finally:
                conn.close()

            total_hours = sum(safe_float(r.get("hours"), 0.0) for r in rows)
            total_earned = sum(
                safe_float(r.get("hours"), 0.0) * safe_float(r.get("hourly_rate"), 0.0)
                for r in rows
            )
            effective = (total_earned / total_hours) if total_hours > 0 else 0.0

            if effective > 0:
                session["wageSource"] = "freelance"
                session["freelanceHourlyRate"] = f"{effective:.2f}"

        return redirect(url_for("freelance", range=range_key))

    # GET view
    conn = get_connection()
    try:
        entries = conn.execute(
            text(
                f"""
                SELECT
                  id,
                  entry_date AS work_date,
                  hours,
                  hourly_rate AS rate,
                  (hours * hourly_rate) AS total,
                  notes,
                  client AS job_name
                FROM freelance_entries
                WHERE {date_col} >= :start
                ORDER BY entry_date DESC, id DESC
                """
            ),
            {"start": start_date},
        ).mappings().all()

    finally:
        conn.close()

    total_hours = round(sum(safe_float(e.get("hours"), 0.0) for e in entries), 2)
    total_earned = round(sum(safe_float(e.get("total"), 0.0) for e in entries), 2)
    effective_rate = round((total_earned / total_hours) if total_hours > 0 else 0.0, 2)

    using_freelance = session.get("wageSource") == "freelance"
    freelance_hourly = session.get("freelanceHourlyRate", "")

    # Breakdown: how much time/earned per "job/client"
    by_client = defaultdict(lambda: {"hours": 0.0, "earned": 0.0})

    for e in entries:
        client = (e.get("job_name") or "Private").strip()
        hours = float(e.get("hours") or 0)
        rate = float(e.get("rate") or 0)
        earned = float(e.get("total") or (hours * rate) or 0)

        by_client[client]["hours"] += hours
        by_client[client]["earned"] += earned

    client_rows = []
    for client, v in by_client.items():
        h = v["hours"]
        earned = v["earned"]
        avg_rate = (earned / h) if h > 0 else 0.0
        equiv_hours = (earned / effective_rate) if effective_rate > 0 else 0.0

        client_rows.append(
            {
                "client": client,
                "hours": round(h, 2),
                "earned": round(earned, 2),
                "avg_rate": round(avg_rate, 2),
                "equiv_hours": round(equiv_hours, 2),
            }
        )

    client_rows.sort(key=lambda r: r["earned"], reverse=True)

    equiv_total_hours = round((total_earned / effective_rate) if effective_rate > 0 else 0.0, 2)

    return render_template(
        "freelance.html",
        currency=_currency(),
        range_key=range_key,
        jobs=[],  # template expects this; keep harmlessly empty for now
        entries=entries,
        total_hours=total_hours,
        total_earned=total_earned,
        effective_rate=effective_rate,
        using_freelance=using_freelance,
        freelance_hourly=freelance_hourly,
        client_rows=client_rows,
        equiv_total_hours=equiv_total_hours,
    )


@app.post("/freelance/add_job")
def freelance_add_job():
    """
    Your current DB schema does not have freelance_jobs.
    Template has an "Add job" form; to avoid breaking it,
    we just redirect back. (Next step: remove job form or add jobs table.)
    """
    return redirect(url_for("freelance"))


@app.post("/freelance/add_entry")
def freelance_add_entry():
    client = (request.form.get("client") or "").strip()

    # Respect privacy: empty means private
    if not client:
        client = "Private"

    work_date = _parse_date((request.form.get("work_date") or "").strip())
    hours = safe_float(request.form.get("hours"), 0.0)
    rate = safe_float(request.form.get("rate"), 0.0)
    notes = (request.form.get("notes") or "").strip()

    if hours <= 0 or rate <= 0:
        return redirect(url_for("freelance"))

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO freelance_entries
                  (entry_date, client, hours, hourly_rate, notes)
                VALUES
                  (:entry_date, :client, :hours, :hourly_rate, :notes)
                """
            ),
            {
                "entry_date": work_date,
                "client": client,
                "hours": hours,
                "hourly_rate": rate,
                "notes": notes or None,
            },
        )

    return redirect(url_for("freelance"))


@app.post("/freelance/delete_entry/<int:entry_id>")
def freelance_delete_entry(entry_id: int):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM freelance_entries WHERE id = :id"), {"id": entry_id})
    return redirect(url_for("freelance"))

def _require_user_key() -> str:
    # You already set this in ensure_identity()
    return session["user_key"]

def _current_household_id() -> Optional[int]:
    hid = session.get("household_id")
    try:
        return int(hid) if hid is not None else None
    except (TypeError, ValueError):
        return None


@app.route("/household", methods=["GET", "POST"])
def household():
    """
    Minimal household join/create.
    - Create: generates an invite code, creates household, adds you as member, stores household_id in session.
    - Join: user enters invite code, we look it up, add them, store household_id in session.
    """
    user_key = _require_user_key()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "create":
            invite_code = secrets.token_urlsafe(6)

            with engine.begin() as conn:
                row = conn.execute(
                    text("INSERT INTO households (invite_code) VALUES (:c) RETURNING id"),
                    {"c": invite_code},
                ).mappings().first()

                # SQLite doesn't support RETURNING on older versions.
                # If RETURNING fails for you locally, swap to: insert then SELECT last_insert_rowid().
                household_id = row["id"] if row else None

                if household_id is None:
                    household_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

                conn.execute(
                    text("""
                        INSERT OR IGNORE INTO household_members (household_id, user_key, display_name)
                        VALUES (:hid, :uk, :dn)
                    """),
                    {"hid": household_id, "uk": user_key, "dn": (session.get("username") or "Me")},
                )

            session["household_id"] = int(household_id)
            session["household_invite_code"] = invite_code
            return redirect(url_for("expenses"))

        if action == "join":
            code = (request.form.get("invite_code") or "").strip()
            if not code:
                return redirect(url_for("household"))

            with engine.begin() as conn:
                hh = conn.execute(
                    text("SELECT id, invite_code FROM households WHERE invite_code = :c"),
                    {"c": code},
                ).mappings().first()

                if not hh:
                    return redirect(url_for("household"))

                household_id = int(hh["id"])

                conn.execute(
                    text("""
                        INSERT OR IGNORE INTO household_members (household_id, user_key, display_name)
                        VALUES (:hid, :uk, :dn)
                    """),
                    {"hid": household_id, "uk": user_key, "dn": (session.get("username") or "Me")},
                )

            session["household_id"] = household_id
            session["household_invite_code"] = code
            return redirect(url_for("expenses"))

    # GET: show current state (template later)
    invite_code = session.get("household_invite_code")
    return render_template("household.html", household_id=_current_household_id(), invite_code=invite_code)



# ----------------------------
# UI toggles
# ----------------------------
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


if __name__ == "__main__":
    app.run(debug=True)
