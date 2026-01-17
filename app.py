from flask import Flask, render_template, request, session, redirect, url_for
from sqlalchemy import text
from database import get_db_connection, init_db
import os
from typing import Optional


def _clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


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
    try:
        total_hours = float(total_hours)
        hours_per_day = float(hours_per_day)
    except (TypeError, ValueError):
        return ""

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


def week_equivalent(total_hours: float, hours_per_week: float = 40.0) -> str:
    try:
        total_hours = float(total_hours)
        hours_per_week = float(hours_per_week)
    except (TypeError, ValueError):
        return ""

    if total_hours <= 0 or hours_per_week <= 0:
        return ""

    weeks = total_hours / hours_per_week
    if weeks < 0.1:
        return "less than a tenth of a workweek"
    return f"about {round(weeks, 1)} workweeks"


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

# Initialize DB on startup (needed for gunicorn/Fly)
try:
    init_db()
except Exception as e:
    print("Database init error:", e)


@app.context_processor
def inject_globals():
    return {
        "currency": session.get("currency", "$"),
        "perspective": session.get("perspective", "river"),
    }


def get_effective_hourly_rate() -> Optional[float]:
    """
    Returns effective hourly rate based on session info.
    Priority:
      1) hourlyRate
      2) annualRate + workHours
      3) paycheckAmount + payFrequency + workHours
    """
    # 1) direct hourly
    try:
        hr = float(session.get("hourlyRate") or 0)
        if hr > 0:
            return hr
    except ValueError:
        pass

    # Hours per week (default 40)
    try:
        weekly_hours = float(session.get("workHours") or 40)
        if weekly_hours <= 0:
            weekly_hours = 40
    except ValueError:
        weekly_hours = 40

    hours_per_year = weekly_hours * 52

    # 2) annual salary
    try:
        annual = float(session.get("annualRate") or 0)
        if annual > 0 and hours_per_year > 0:
            return annual / hours_per_year
    except ValueError:
        pass

    # 3) paycheck conversion
    try:
        paycheck = float(session.get("paycheckAmount") or 0)
        freq = (session.get("payFrequency") or "").lower()

        if paycheck > 0 and freq in {"weekly", "biweekly", "monthly"}:
            if freq == "weekly":
                annual = paycheck * 52
            elif freq == "biweekly":
                annual = paycheck * 26
            else:  # monthly
                annual = paycheck * 12

            if hours_per_year > 0:
                return annual / hours_per_year
    except ValueError:
        pass

    return None


def _get_weekly_hours_default40() -> float:
    try:
        weekly = float(session.get("workHours") or 40)
        return weekly if weekly > 0 else 40.0
    except (TypeError, ValueError):
        return 40.0


def _hourly_from_wage(wage_amount: float, wage_type: str) -> float:
    """
    Convert user-entered wage to hourly, using Personal workHours/week.
    """
    weekly_hours = _get_weekly_hours_default40()
    hours_per_year = weekly_hours * 52
    hours_per_month = hours_per_year / 12

    if wage_type == "hourly":
        return wage_amount
    if wage_type == "weekly":
        return wage_amount / weekly_hours
    if wage_type == "biweekly":
        return wage_amount / (weekly_hours * 2)
    if wage_type == "monthly":
        return wage_amount / hours_per_month
    if wage_type == "annual":
        return wage_amount / hours_per_year

    # fallback
    return wage_amount


def _prefill_wage_from_personal() -> tuple[str, str]:
    """
    Decide what wageType + wageAmount should show on the Calculator
    based on Personal session values.

    Priority:
      1) hourlyRate
      2) annualRate
      3) paycheckAmount + payFrequency
    """
    hourly_rate = (session.get("hourlyRate") or "").strip()
    annual_rate = (session.get("annualRate") or "").strip()
    paycheck_amount = (session.get("paycheckAmount") or "").strip()
    pay_frequency = (session.get("payFrequency") or "").strip().lower()

    if hourly_rate:
        return "hourly", hourly_rate
    if annual_rate:
        return "annual", annual_rate
    if paycheck_amount and pay_frequency in {"weekly", "biweekly", "monthly"}:
        return pay_frequency, paycheck_amount

    return "hourly", ""


@app.route("/", methods=["GET", "POST"])
def calculator():
    # Prefill inputs from Personal
    pre_wage_type, pre_wage_amount = _prefill_wage_from_personal()

    result = None
    item_name = ""
    item_cost = ""
    time_cost = None
    workday_text = ""

    if request.method == "POST":
        item_name = request.form.get("itemName", "")
        item_cost = (request.form.get("itemCost") or "").strip()

        wage_type = (request.form.get("wageType") or pre_wage_type).strip().lower()
        wage_amount_raw = (request.form.get("wageAmount") or "").strip()

        try:
            item_cost_f = float(item_cost)
            if item_cost_f < 0:
                raise ValueError("Cost can't be negative")

            # If wage left blank, fall back to Personal effective hourly
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
        time_cost=time_cost,
        workday_text=workday_text,
    )


@app.route("/personal", methods=["GET", "POST"])
def personal():
    if request.method == "POST":
        session["username"] = (request.form.get("username") or "").strip()
        session["workHours"] = request.form.get("workHours")

        allowed_currencies = {"$", "£", "€", "¥", "₹", "₩", "₽"}
        c = request.form.get("currency", "$")
        session["currency"] = c if c in allowed_currencies else "$"

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

    conn = get_db_connection()
    try:
        rows = conn.execute(text("SELECT amount FROM expenses")).mappings().all()
        expenses_total = sum((row["amount"] or 0) for row in rows)
    except Exception:
        expenses_total = 0
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
        currency=session.get("currency", "$"),
    )


@app.route("/timebank", methods=["GET", "POST"])
def timebank():
    currency = session.get("currency", "$")

    if request.method == "POST":
        try:
            income = float(request.form.get("income", 0))
            expenses = float(request.form.get("expenses", 0))
            hoursWorked = float(request.form.get("hoursWorked", 0))
        except ValueError:
            income = expenses = hoursWorked = 0

        conn = get_db_connection()
        try:
            savings_row = conn.execute(
                text("SELECT SUM(amount) AS total FROM expenses WHERE category = 'Savings'")
            ).mappings().first()
            savings_value = (savings_row["total"] if savings_row else 0) or 0
        except Exception:
            savings_value = 0
        finally:
            conn.close()

        return render_template(
            "timebank.html",
            income=income,
            expenses=expenses,
            hoursWorked=hoursWorked,
            savings_value=savings_value,
            currency=currency,
        )

    try:
        income = float(session.get("annualRate", 0)) / 12
    except (ValueError, TypeError):
        income = 0

    conn = get_db_connection()
    try:
        all_rows = conn.execute(text("SELECT amount, category FROM expenses")).mappings().all()
    except Exception:
        all_rows = []
    finally:
        conn.close()

    expenses_total = sum((row["amount"] or 0) for row in all_rows)
    savings_value = sum((row["amount"] or 0) for row in all_rows if row["category"] == "Savings")

    try:
        hoursWorked = float(session.get("workHours", 0))
    except (ValueError, TypeError):
        hoursWorked = 0

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
    conn = get_db_connection()
    try:
        if request.method == "POST":
            expense_names = request.form.getlist("expense_name[]")
            expense_amounts = request.form.getlist("expense_amount[]")
            expense_categories = request.form.getlist("expense_category[]")

            conn.execute(text("DELETE FROM expenses"))

            for name, amount, category in zip(expense_names, expense_amounts, expense_categories):
                try:
                    amt = float(amount)
                    if name.strip() and category:
                        conn.execute(
                            text(
                                "INSERT INTO expenses (name, amount, category) "
                                "VALUES (:name, :amount, :category)"
                            ),
                            {"name": name.strip(), "amount": amt, "category": category},
                        )
                except ValueError:
                    continue

            conn.commit()
            return redirect(url_for("expenses"))

        saved_expenses = conn.execute(text("SELECT * FROM expenses")).mappings().all()
        category_totals = conn.execute(
            text("SELECT category, SUM(amount) AS total FROM expenses GROUP BY category")
        ).mappings().all()

    finally:
        conn.close()

    currency = session.get("currency", "$")
    return render_template(
        "expenses.html",
        saved_expenses=saved_expenses,
        category_totals=category_totals,
        currency=currency,
    )


@app.route("/update_expense_category", methods=["POST"])
def update_expense_category():
    expense_id = request.form.get("expense_id")
    new_category = request.form.get("new_category")
    if not expense_id or not new_category:
        return redirect(url_for("expenses"))

    conn = get_db_connection()
    try:
        conn.execute(
            text("UPDATE expenses SET category = :cat WHERE id = :id"),
            {"cat": new_category, "id": int(expense_id)},
        )
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("expenses"))


@app.route("/remove_expense/<int:index>", methods=["POST"])
def remove_expense(index):
    conn = get_db_connection()
    try:
        conn.execute(text("DELETE FROM expenses WHERE id = :id"), {"id": index})
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("expenses"))


@app.route("/budget", methods=["GET", "POST"])
def budget():
    currency = session.get("currency", "$")

    conn = get_db_connection()
    try:
        expenses_rows = conn.execute(text("SELECT amount FROM expenses")).mappings().all()
        expenses_total = sum((row["amount"] or 0) for row in expenses_rows)
    except Exception:
        expenses_total = 0
    finally:
        conn.close()

    if request.method == "POST":
        try:
            income_input = request.form.get("income")
            income = float(income_input) if income_input else float(session.get("annualRate", 0)) / 12

            hours_input = request.form.get("weeklyHours")
            weekly_hours = float(hours_input) if hours_input else float(session.get("workHours", 0))
            monthly_hours = weekly_hours * 4.33 if weekly_hours else 1

            discretionary_income = income - expenses_total
            hourly_value = discretionary_income / monthly_hours if monthly_hours else 0

            savings_goal = float(request.form.get("savingsGoal", 0))
            current_savings = float(request.form.get("currentSavings", 0))
            remaining_to_save = savings_goal - current_savings if savings_goal > 0 else 0
            progress_percent = (current_savings / savings_goal) * 100 if savings_goal > 0 else 0

        except ValueError:
            income = weekly_hours = monthly_hours = discretionary_income = hourly_value = 0
            savings_goal = current_savings = remaining_to_save = progress_percent = 0

        conn = get_db_connection()
        try:
            goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
        except Exception:
            goals_rows = []
        finally:
            conn.close()

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

    try:
        income = float(session.get("annualRate", 0)) / 12
        weekly_hours = float(session.get("workHours", 0))
        monthly_hours = weekly_hours * 4.33 if weekly_hours else 1
        discretionary_income = income - expenses_total
        hourly_value = discretionary_income / monthly_hours if monthly_hours else 0
    except ValueError:
        income = weekly_hours = monthly_hours = discretionary_income = hourly_value = 0

    savings_goal = current_savings = remaining_to_save = progress_percent = 0

    conn = get_db_connection()
    try:
        goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
    except Exception:
        goals_rows = []
    finally:
        conn.close()

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


@app.route("/goals", methods=["GET", "POST"])
def goals():
    conn = get_db_connection()
    try:
        if request.method == "POST":
            if "new_goal" in request.form:
                name = (request.form.get("goal_name") or "").strip()
                target = float(request.form.get("target_amount", 0) or 0)
                current = float(request.form.get("current_savings", 0) or 0)
                if name:
                    conn.execute(
                        text("INSERT INTO goals (name, target, current) VALUES (:n,:t,:c)"),
                        {"n": name, "t": target, "c": current},
                    )
                    conn.commit()

            elif "update_goal" in request.form:
                goal_id = int(request.form.get("goal_index", 0) or 0)
                add_amount = float(request.form.get("savings_to_add", 0) or 0)

                goal = conn.execute(
                    text("SELECT * FROM goals WHERE id = :id"),
                    {"id": goal_id},
                ).mappings().first()

                if goal:
                    new_total = float(goal["current"]) + add_amount
                    conn.execute(
                        text("UPDATE goals SET current = :c WHERE id = :id"),
                        {"c": new_total, "id": goal_id},
                    )
                    conn.commit()

            return redirect(url_for("goals"))

        goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
    finally:
        conn.close()

    currency = session.get("currency", "$")
    return render_template("goals.html", goals=goals_rows, currency=currency)


@app.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    conn = get_db_connection()
    try:
        conn.execute(text("DELETE FROM goals WHERE id = :id"), {"id": goal_id})
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("goals"))


@app.route("/staples", methods=["GET"])
def staples():
    return render_template("staples.html")


@app.route("/set_currency", methods=["POST"])
def set_currency():
    allowed = {"$", "£", "€", "¥", "₹", "₩", "₽"}
    c = request.form.get("currency", "$")
    session["currency"] = c if c in allowed else "$"
    return redirect(request.referrer or url_for("personal"))


@app.route("/set_perspective", methods=["POST"])
def set_perspective():
    allowed = {"river", "leslie", "eddie"}
    p = request.form.get("perspective", "river")
    session["perspective"] = p if p in allowed else "river"
    return redirect(request.referrer or url_for("calculator"))
