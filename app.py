import os
from flask import Flask, render_template, request, session, redirect, url_for
from sqlalchemy import text

from database import get_db_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))


# -----------------------
# TimeCost Calculator
# -----------------------
@app.route("/", methods=["GET", "POST"])
def calculator():
    pre_wage_amount = session.get("hourlyRate", "")
    pre_wage_type = "hourly"

    result = None
    item_name = ""
    item_cost_value = ""

    if request.method == "POST":
        currency = request.form.get("currency", "$")
        session["currency"] = currency

        item_name = request.form.get("itemName", "")
        item_cost_raw = request.form.get("itemCost")
        item_cost_value = item_cost_raw or ""

        wage_type = request.form.get("wageType") or pre_wage_type
        wage_amount_raw = request.form.get("wageAmount") or pre_wage_amount

        try:
            item_cost = float(item_cost_raw)
            wage_amount = float(wage_amount_raw)

            if wage_amount <= 0:
                raise ValueError("Wage must be > 0")

            if wage_type == "annual":
                wage_amount /= 2080
            elif wage_type == "monthly":
                wage_amount /= 173.33

            if wage_amount <= 0:
                raise ValueError("Converted wage must be > 0")

            result = item_cost / wage_amount
            pre_wage_amount = wage_amount_raw  # keep user's input visible
        except (ValueError, TypeError, ZeroDivisionError):
            result = "Invalid input"

        return render_template(
            "calculator.html",
            result=result,
            item_name=item_name,
            item_cost=item_cost_value,
            pre_wage_type=wage_type,
            pre_wage_amount=wage_amount_raw,
            currency=currency,
        )

    return render_template(
        "calculator.html",
        result=None,
        item_name="",
        item_cost="",
        pre_wage_type=pre_wage_type,
        pre_wage_amount=pre_wage_amount,
        currency=session.get("currency", "$"),
    )


# -----------------------
# Personal Information
# -----------------------
@app.route("/personal", methods=["GET", "POST"])
def personal():
    if request.method == "POST":
        session["username"] = (request.form.get("username") or "").strip()
        session["currency"] = request.form.get("currency", "$")
        session["payFrequency"] = request.form.get("payFrequency", "")
        session["paycheckAmount"] = request.form.get("paycheckAmount", "")

        work_hours_raw = request.form.get("workHours", 40)
        try:
            session["workHours"] = float(work_hours_raw)
        except (ValueError, TypeError):
            session["workHours"] = 40.0

        annual_raw = request.form.get("annualRate")
        hourly_raw = request.form.get("hourlyRate")

        def _clean_money(v):
            if v is None:
                return ""
            v = str(v).strip()
            if v == "":
                return ""
            try:
                x = float(v)
                return "" if x <= 0 else str(x)
            except ValueError:
                return ""

        session["annualRate"] = _clean_money(annual_raw)
        session["hourlyRate"] = _clean_money(hourly_raw)

        def _as_float(v):
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        paycheck = _as_float(session.get("paycheckAmount"))
        freq = session.get("payFrequency")
        work_hours_week = _as_float(session.get("workHours")) or 40.0

        annual_est = None
        if paycheck and paycheck > 0 and freq:
            if freq == "weekly":
                annual_est = paycheck * 52
            elif freq == "biweekly":
                annual_est = paycheck * 26
            elif freq == "monthly":
                annual_est = paycheck * 12

        if (not session.get("annualRate")) and annual_est:
            session["annualRate"] = str(annual_est)

        annual_for_hourly = _as_float(session.get("annualRate"))
        if (not session.get("hourlyRate")) and annual_for_hourly and annual_for_hourly > 0:
            denom = 52 * work_hours_week
            if denom > 0:
                session["hourlyRate"] = str(annual_for_hourly / denom)

        return redirect(url_for("calculator"))

    # GET: expenses total from DB
    conn = get_db_connection()
    try:
        rows = conn.execute(text("SELECT amount FROM expenses")).mappings().all()
        expenses_total = sum((row["amount"] or 0) for row in rows)
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


# -----------------------
# Timebank
# -----------------------
@app.route("/timebank", methods=["GET", "POST"])
def timebank():
    currency = session.get("currency", "$")

    # Always load savings_value from DB (source of truth)
    conn = get_db_connection()
    try:
        savings_row = conn.execute(
            text("SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE category = 'Savings'")
        ).mappings().first()
        savings_value = (savings_row["total"] if savings_row else 0) or 0

        all_rows = conn.execute(text("SELECT amount, category FROM expenses")).mappings().all()
        expenses_total = sum((r["amount"] or 0) for r in all_rows)
    finally:
        conn.close()

    if request.method == "POST":
        try:
            income = float(request.form.get("income", 0))
            expenses = float(request.form.get("expenses", expenses_total))
            hoursWorked = float(request.form.get("hoursWorked", 0))
        except ValueError:
            income = 0
            expenses = expenses_total
            hoursWorked = 0

        return render_template(
            "timebank.html",
            income=income,
            expenses=expenses,
            hoursWorked=hoursWorked,
            savings_value=savings_value,
            currency=currency,
        )

    # GET defaults
    try:
        income = float(session.get("annualRate", 0)) / 12
    except (ValueError, TypeError):
        income = 0

    try:
        hoursWorked = float(session.get("workHours", 0)) * 4.33  # convert weekly to monthly-ish
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


# -----------------------
# Expenses
# -----------------------
@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    currency = session.get("currency", "$")
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
                            text("INSERT INTO expenses (name, amount, category) VALUES (:name, :amount, :category)"),
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

        return render_template(
            "expenses.html",
            saved_expenses=saved_expenses,
            category_totals=category_totals,
            currency=currency,
        )
    finally:
        conn.close()


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


# -----------------------
# Budget
# -----------------------
@app.route("/budget", methods=["GET", "POST"])
def budget():
    currency = session.get("currency", "$")

    # Expenses from DB (source of truth)
    conn = get_db_connection()
    try:
        expenses_rows = conn.execute(text("SELECT amount FROM expenses")).mappings().all()
        expenses_total = sum((row["amount"] or 0) for row in expenses_rows)
    finally:
        conn.close()

    def _as_float(v, default=0.0):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    if request.method == "POST":
        income_input = request.form.get("income")
        income = _as_float(income_input, _as_float(session.get("annualRate"), 0) / 12)

        weekly_hours = _as_float(request.form.get("weeklyHours"), _as_float(session.get("workHours"), 0))
        monthly_hours = weekly_hours * 4.33 if weekly_hours else 1

        discretionary_income = income - expenses_total
        hourly_value = discretionary_income / monthly_hours if monthly_hours else 0

        savings_goal = _as_float(request.form.get("savingsGoal"), 0)
        current_savings = _as_float(request.form.get("currentSavings"), 0)

        conn = get_db_connection()
        try:
            goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
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
            remaining_to_save=(savings_goal - current_savings) if savings_goal > 0 else 0,
            progress_percent=(current_savings / savings_goal * 100) if savings_goal > 0 else 0,
            goals=goals_rows,
            currency=currency,
        )

    # GET defaults
    income = _as_float(session.get("annualRate"), 0) / 12
    weekly_hours = _as_float(session.get("workHours"), 0)
    monthly_hours = weekly_hours * 4.33 if weekly_hours else 1
    discretionary_income = income - expenses_total
    hourly_value = discretionary_income / monthly_hours if monthly_hours else 0

    conn = get_db_connection()
    try:
        goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
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
        savings_goal=0,
        current_savings=0,
        remaining_to_save=0,
        progress_percent=0,
        goals=goals_rows,
        currency=currency,
    )


# -----------------------
# Goals
# -----------------------
@app.route("/goals", methods=["GET", "POST"])
def goals():
    currency = session.get("currency", "$")
    conn = get_db_connection()
    try:
        if request.method == "POST":
            if "new_goal" in request.form:
                name = (request.form.get("goal_name") or "").strip()
                try:
                    target = float(request.form.get("target_amount", 0))
                except ValueError:
                    target = 0
                try:
                    current = float(request.form.get("current_savings", 0))
                except ValueError:
                    current = 0

                if name:
                    conn.execute(
                        text("INSERT INTO goals (name, target, current) VALUES (:name, :target, :current)"),
                        {"name": name, "target": target, "current": current},
                    )
                    conn.commit()

            elif "update_goal" in request.form:
                try:
                    goal_id = int(request.form.get("goal_index"))
                    add_amount = float(request.form.get("savings_to_add", 0))
                except (ValueError, TypeError):
                    goal_id = None
                    add_amount = 0

                if goal_id is not None:
                    goal = conn.execute(
                        text("SELECT * FROM goals WHERE id = :id"),
                        {"id": goal_id},
                    ).mappings().first()

                    if goal:
                        new_total = float(goal["current"]) + add_amount
                        conn.execute(
                            text("UPDATE goals SET current = :current WHERE id = :id"),
                            {"current": new_total, "id": goal_id},
                        )
                        conn.commit()

            return redirect(url_for("goals"))

        goals_rows = conn.execute(text("SELECT * FROM goals")).mappings().all()
        return render_template("goals.html", goals=goals_rows, currency=currency)
    finally:
        conn.close()


@app.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    conn = get_db_connection()
    try:
        conn.execute(text("DELETE FROM goals WHERE id = :id"), {"id": goal_id})
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("goals"))


@app.route("/set_currency", methods=["POST"])
def set_currency():
    selected = request.form.get("currency", "$")
    session["currency"] = selected
    return redirect(request.referrer or url_for("personal"))


@app.route("/staples", methods=["GET"])
def staples():
    return render_template(
        "staples.html",
        currency=session.get("currency", "$"),
        hourlyRate=session.get("hourlyRate", "")
    )


# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    # Run init locally only
    init_db()
    app.run(debug=True)
