from flask import Flask, render_template, request, session, redirect, url_for
from database import get_db_connection, init_db

try:
    init_db()
except Exception as e:
    print("Database init error:", e)

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # Replace with a secure key in production


# TimeCost Calculator Route
@app.route("/", methods=["GET", "POST"])
def calculator():
    pre_wage_amount = session.get("hourlyRate", "")
    pre_wage_type = "hourly"
    result = None
    item_name = ""

    if request.method == "POST":
        currency = request.form.get("currency", "$")
        session["currency"] = currency  # Save currency for reuse

        item_name = request.form.get("itemName")
        item_cost = request.form.get("itemCost")
        wage_type = request.form.get("wageType") or pre_wage_type
        wage_amount = request.form.get("wageAmount") or pre_wage_amount

        try:
            item_cost = float(item_cost)
            wage_amount = float(wage_amount)

            if wage_type == "annual":
                wage_amount /= 2080
            elif wage_type == "monthly":
                wage_amount /= 173.33

            result = item_cost / wage_amount
        except (ValueError, TypeError):
            result = "Invalid input"

        return render_template("calculator.html",
                               result=result,
                               item_name=item_name,
                               pre_wage_type=wage_type,
                               pre_wage_amount=wage_amount,
                               currency=currency)

    return render_template("calculator.html",
                           result=None,
                           item_name="",
                           pre_wage_type=pre_wage_type,
                           pre_wage_amount=pre_wage_amount,
                           currency=session.get("currency", "$"))


# Personal Information Page
@app.route("/personal", methods=["GET", "POST"])
def personal():
    if request.method == "POST":
        # (same logic for paycheck, name, etc.)
        session["username"] = request.form.get("username")
        session["workHours"] = request.form.get("workHours")
        session["currency"] = request.form.get("currency", "$")

        # Do NOT store monthlyExpenses to session
        # session["expenses"] = request.form.get("monthlyExpenses")  ← REMOVE THIS LINE

        # (rest of logic unchanged...)
        return redirect(url_for("calculator"))

    # GET: Pull expenses total from DB
    conn = get_db_connection()
    rows = conn.execute("SELECT amount FROM expenses").fetchall()
    conn.close()
    expenses = sum(row["amount"] for row in rows)

    return render_template("personal.html",
                           username=session.get("username", ""),
                           annualRate=session.get("annualRate", ""),
                           hourlyRate=session.get("hourlyRate", ""),
                           workHours=session.get("workHours", 40),
                           expenses=expenses,
                           paycheckAmount=session.get("paycheckAmount", ""),
                           payFrequency=session.get("payFrequency", ""),
                           currency=session.get("currency"))

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

        # Query savings value from category
        conn = get_db_connection()
        savings_row = conn.execute("SELECT SUM(amount) as total FROM expenses WHERE category = 'Savings'").fetchone()
        conn.close()
        savings_value = savings_row["total"] or 0

        return render_template("timebank.html",
                               income=income,
                               expenses=expenses,
                               hoursWorked=hoursWorked,
                               savings_value=savings_value,
                               currency=currency)

    # GET: load from session defaults
    try:
        income = float(session.get("annualRate", 0)) / 12
    except (ValueError, TypeError):
        income = 0

    # Get all expenses and savings
    conn = get_db_connection()
    all_rows = conn.execute("SELECT amount, category FROM expenses").fetchall()
    conn.close()

    expenses = sum(row["amount"] for row in all_rows)
    savings_value = sum(row["amount"] for row in all_rows if row["category"] == "Savings")

    try:
        hoursWorked = float(session.get("workHours", 0))
    except (ValueError, TypeError):
        hoursWorked = 0

    return render_template("timebank.html",
                           income=income,
                           expenses=expenses,
                           hoursWorked=hoursWorked,
                           savings_value=savings_value,
                           currency=currency)


@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    conn = get_db_connection()

    if request.method == "POST":
        # Collect form data
        expense_names = request.form.getlist("expense_name[]")
        expense_amounts = request.form.getlist("expense_amount[]")
        expense_categories = request.form.getlist("expense_category[]")

        # Clear old expenses (or modify logic to append if desired)
        conn.execute("DELETE FROM expenses")

        for name, amount, category in zip(expense_names, expense_amounts, expense_categories):
            try:
                amount = float(amount)
                if name.strip() and category:
                    conn.execute("INSERT INTO expenses (name, amount, category) VALUES (?, ?, ?)",
                                 (name.strip(), amount, category))
            except ValueError:
                continue

        conn.commit()
        conn.close()
        return redirect(url_for("expenses"))

    # GET: retrieve saved expenses
    saved_expenses = conn.execute("SELECT * FROM expenses").fetchall()

    # Calculate totals grouped by category
    category_totals = conn.execute("""
        SELECT category, SUM(amount) AS total
        FROM expenses
        GROUP BY category
    """).fetchall()

    conn.close()

    currency = session.get("currency", "$")
    return render_template("expenses.html",
                           saved_expenses=saved_expenses,
                           category_totals=category_totals,
                           currency=currency)


@app.route("/update_expense_category", methods=["POST"])
def update_expense_category():
    expense_id = request.form.get("expense_id")
    new_category = request.form.get("new_category")

    try:
        conn = get_db_connection()
        conn.execute("UPDATE expenses SET category = ? WHERE id = ?", (new_category, expense_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Error updating category:", e)

    return redirect(url_for("expenses"))


@app.route("/remove_expense/<int:index>", methods=["POST"])
def remove_expense(index):
    conn = get_db_connection()
    conn.execute("DELETE FROM expenses WHERE id = ?", (index,))
    conn.commit()
    conn.close()
    return redirect(url_for("expenses"))


@app.route("/budget", methods=["GET", "POST"])
def budget():
    currency = session.get("currency", "$")

    # Step 1: Fetch total expenses from database
    conn = get_db_connection()
    expenses_rows = conn.execute("SELECT amount FROM expenses").fetchall()
    expenses = sum(row["amount"] for row in expenses_rows)
    conn.close()

    if request.method == "POST":
        try:
            # Step 2: Get form inputs with fallback to session
            income_input = request.form.get("income")
            income = float(income_input) if income_input else float(session.get("annualRate", 0)) / 12

            hours_input = request.form.get("weeklyHours")
            weekly_hours = float(hours_input) if hours_input else float(session.get("workHours", 0))
            monthly_hours = weekly_hours * 4.33 if weekly_hours else 1

            # Step 3: Calculations
            discretionary_income = income - expenses
            hourly_value = discretionary_income / monthly_hours if monthly_hours else 0

            # Step 4: Savings goals input
            savings_goal = float(request.form.get("savingsGoal", 0))
            current_savings = float(request.form.get("currentSavings", 0))
            remaining_to_save = savings_goal - current_savings if savings_goal > 0 else 0
            progress_percent = (current_savings / savings_goal) * 100 if savings_goal > 0 else 0

        except ValueError as e:
            print("Error:", e)
            income = weekly_hours = monthly_hours = discretionary_income = hourly_value = 0
            savings_goal = current_savings = remaining_to_save = progress_percent = 0

        # Step 5: Retrieve goals from DB
        conn = get_db_connection()
        goals = conn.execute("SELECT * FROM goals").fetchall()
        conn.close()

        return render_template("budget.html",
                               income=income,
                               expenses=expenses,
                               weekly_hours=weekly_hours,
                               monthly_hours=monthly_hours,
                               discretionary_income=discretionary_income,
                               hourly_value=hourly_value,
                               savings_goal=savings_goal,
                               current_savings=current_savings,
                               remaining_to_save=remaining_to_save,
                               progress_percent=progress_percent,
                               goals=goals,
                               currency=currency)

    # GET request: prepopulate from session
    try:
        income = float(session.get("annualRate", 0)) / 12
        weekly_hours = float(session.get("workHours", 0))
        monthly_hours = weekly_hours * 4.33 if weekly_hours else 1
        discretionary_income = income - expenses
        hourly_value = discretionary_income / monthly_hours if monthly_hours else 0
    except ValueError:
        income = weekly_hours = monthly_hours = discretionary_income = hourly_value = 0

    savings_goal = current_savings = remaining_to_save = progress_percent = 0

    # Get goals again
    conn = get_db_connection()
    goals = conn.execute("SELECT * FROM goals").fetchall()
    conn.close()

    return render_template("budget.html",
                           income=income,
                           expenses=expenses,
                           weekly_hours=weekly_hours,
                           monthly_hours=monthly_hours,
                           discretionary_income=discretionary_income,
                           hourly_value=hourly_value,
                           savings_goal=savings_goal,
                           current_savings=current_savings,
                           remaining_to_save=remaining_to_save,
                           progress_percent=progress_percent,
                           goals=goals,
                           currency=currency)

@app.route("/goals", methods=["GET", "POST"])
def goals():
    conn = get_db_connection()

    if request.method == "POST":
        if "new_goal" in request.form:
            name = request.form.get("goal_name", "").strip()
            target = float(request.form.get("target_amount", 0))
            current = float(request.form.get("current_savings", 0))
            if name:
                conn.execute("INSERT INTO goals (name, target, current) VALUES (?, ?, ?)",
                             (name, target, current))
                conn.commit()

        elif "update_goal" in request.form:
            try:
                index = int(request.form.get("goal_index"))
                add_amount = float(request.form.get("savings_to_add", 0))
                goal = conn.execute("SELECT * FROM goals WHERE id = ?", (index,)).fetchone()
                if goal:
                    new_total = goal["current"] + add_amount
                    conn.execute("UPDATE goals SET current = ? WHERE id = ?", (new_total, index))
                    conn.commit()
            except ValueError:
                pass

        conn.close()  # ✅ Close after POST before redirect
        return redirect(url_for("goals"))

    # GET request
    goals = conn.execute("SELECT * FROM goals").fetchall()
    conn.close()
    currency = session.get("currency", "$")
    return render_template("goals.html", goals=goals, currency=currency)



@app.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("goals"))

@app.route("/set_currency", methods=["POST"])
def set_currency():
    selected = request.form.get("currency", "$")
    session["currency"] = selected
    return redirect(request.referrer or url_for("personal"))


@app.route("/staples", methods=["GET"])
def staples():
    return render_template("staples.html")

@app.route("/store", methods=["GET", "POST"])
def store():
    if request.method == "POST":
        key = request.form.get("key")
        value = request.form.get("value")
        with shelve.open("data.db") as db:
            db[key] = value  # Save the value with the specified key.
        return redirect(url_for("store"))
    return render_template("store.html")  # Create a store.html template with a form.

@app.route("/retrieve")
def retrieve():
    with shelve.open("data.db") as db:
        # For simplicity, let's just show all keys and their values.
        data = dict(db)
    return f"Data in shelve: {data}"

if __name__ == "__main__":
    app.run(debug=True)
