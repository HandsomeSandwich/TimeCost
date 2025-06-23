from flask import Flask, render_template, request, session, redirect, url_for
import shelve

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # Replace with a secure key in production


# TimeCost Calculator Route
@app.route("/", methods=["GET", "POST"])
def calculator():
    # Use the stored hourly rate from personal info, if available.
    pre_wage_amount = session.get("hourlyRate", "")
    pre_wage_type = "hourly"  # We assume the calculator uses hourly rate

    if request.method == "POST":
        item_name = request.form.get("itemName")
        item_cost = request.form.get("itemCost")
        wage_type = request.form.get("wageType") or pre_wage_type
        wage_amount = request.form.get("wageAmount") or pre_wage_amount
        result = None

        if item_cost and wage_amount:
            try:
                item_cost = float(item_cost)
                wage_amount = float(wage_amount)
                # If by any chance wage type is annual, convert it.
                if wage_type == "annual":
                    wage_amount = wage_amount / 2080  # 2080 working hours per year.
                result = item_cost / wage_amount
            except ValueError:
                result = "Invalid input"
        return render_template(
            "calculator.html",
            result=result,
            item_name=item_name,
            pre_wage_type=wage_type,
            pre_wage_amount=wage_amount,
        )
    return render_template(
        "calculator.html", pre_wage_type=pre_wage_type, pre_wage_amount=pre_wage_amount
    )


# Personal Information Page
@app.route("/personal", methods=["GET", "POST"])
def personal():
    if request.method == "POST":
        # (Your existing code to retrieve and save personal info)
        monthly_expenses = request.form.get("monthlyExpenses")  # if provided

        # In this version, you might not be updating expense items from the expenses page.
        # So if monthlyExpenses is submitted from this form, save it directly:
        session["expenses"] = monthly_expenses

        return redirect(url_for("calculator"))

    # Prepopulate fields using values stored in the session.
    username = session.get("username", "")
    annual_rate = session.get("annualRate", "")
    hourly_rate = session.get("hourlyRate", "")
    work_hours = session.get("workHours", 40)

    # If expenses were saved with individual items, extract the total:
    expenses_data = session.get("expenses", {})
    if isinstance(expenses_data, dict):
        expenses = expenses_data.get("total", "")
    else:
        expenses = expenses_data

    return render_template("personal.html",
                           username=username,
                           annualRate=annual_rate,
                           hourlyRate=hourly_rate,
                           workHours=work_hours,
                           expenses=expenses)


@app.route("/timebank", methods=["GET", "POST"])
def timebank():
    if request.method == "POST":
        income = request.form.get("income")
        expenses = request.form.get("expenses")
        hoursWorked = request.form.get("hoursWorked")
        return render_template("timebank.html", income=income, expenses=expenses, hoursWorked=hoursWorked)
    else:
        monthly_income = ""
        if "annualRate" in session and session["annualRate"]:
            try:
                annual_rate = float(session["annualRate"])
                monthly_income = annual_rate / 12
            except ValueError:
                monthly_income = ""
        # Extract the total from the stored dictionary if necessary.
        expenses_data = session.get("expenses", {})
        if isinstance(expenses_data, dict):
            expenses_total = expenses_data.get("total", "")
        else:
            expenses_total = expenses_data

        hours_worked = session.get("workHours", "")
        return render_template("timebank.html", income=monthly_income, expenses=expenses_total, hoursWorked=hours_worked)


@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    # Retrieve any previously saved expenses from the session.
    # If nothing exists or if the session data isn't in the correct format,
    # initialize with a default dictionary.
    existing_expenses = session.get("expenses", {})
    if not isinstance(existing_expenses, dict):
        existing_expenses = {"total": 0, "expense_items": []}

    if request.method == "POST":
        # Retrieve the newly submitted expense items from the form.
        expense_names = request.form.getlist("expense_name[]")
        expense_amounts = request.form.getlist("expense_amount[]")

        # Build a list of new expense items from the form input.
        new_expense_items = list(zip(expense_names, expense_amounts))

        # Calculate the total for the new expense items.
        try:
            new_total = sum(float(amount) for amount in expense_amounts if amount)
        except ValueError:
            new_total = 0

        # Append the new items to the existing list.
        existing_items = existing_expenses.get("expense_items", [])
        existing_items.extend(new_expense_items)

        # Recalculate the grand total from all the saved items.
        total = 0
        for _name, amount in existing_items:
            try:
                total += float(amount)
            except ValueError:
                continue

        # Update the session dictionary with the new data.
        existing_expenses["total"] = total
        existing_expenses["expense_items"] = existing_items
        session["expenses"] = existing_expenses

        # Redirect (for example, back to the personal page) so that
        # the overall expense total is displayed elsewhere.
        return redirect(url_for("personal"))

    return render_template("expenses.html")


@app.route("/remove_expense/<int:index>", methods=["POST"])
def remove_expense(index):
    expenses_data = session.get("expenses", {})
    expense_items = expenses_data.get("expense_items", [])
    if 0 <= index < len(expense_items):
        expense_items.pop(index)
        try:
            total = sum(float(amount) for (_name, amount) in expense_items if amount)
        except ValueError:
            total = 0
        expenses_data["total"] = total
        expenses_data["expense_items"] = expense_items
        session["expenses"] = expenses_data
    return redirect(url_for("expenses"))


@app.route("/budget", methods=["GET", "POST"])
def budget():
    if request.method == "POST":
        try:
            # Get monthly income: use form-provided value or compute from annualRate.
            if request.form.get("income"):
                income = float(request.form.get("income", 0))
            elif "annualRate" in session and session["annualRate"]:
                income = float(session["annualRate"]) / 12
            else:
                income = 0

            # Get expenses from form.
            expenses = float(request.form.get("expenses", 0))

            # Get weekly hours from form or session.
            if request.form.get("weeklyHours"):
                weekly_hours = float(request.form.get("weeklyHours"))
            elif "workHours" in session and session["workHours"]:
                weekly_hours = float(session["workHours"])
            else:
                weekly_hours = 0

            # Convert weekly hours to monthly hours (using a conversion factor).
            monthly_hours = weekly_hours * 4.33 if weekly_hours else 1  # default to 1 to prevent division by zero

            discretionary_income = income - expenses
            hourly_value = discretionary_income / monthly_hours

            # Additional savings goal values.
            savings_goal = float(request.form.get("savingsGoal", 0))
            current_savings = float(request.form.get("currentSavings", 0))
            if savings_goal > 0:
                remaining_to_save = savings_goal - current_savings
                progress_percent = (current_savings / savings_goal) * 100
            else:
                remaining_to_save = 0
                progress_percent = 0
        except ValueError as e:
            print("Error:", e)
            income = expenses = discretionary_income = hourly_value = savings_goal = current_savings = remaining_to_save = progress_percent = 0
            weekly_hours = 0

        # Retrieve long-term goals from session.
        goals = session.get("goals", [])

        return render_template("budget.html",
                               income=income,
                               expenses=expenses,
                               weekly_hours=weekly_hours,
                               discretionary_income=discretionary_income,
                               hourly_value=hourly_value,
                               savings_goal=savings_goal,
                               current_savings=current_savings,
                               remaining_to_save=remaining_to_save,
                               progress_percent=progress_percent,
                               goals=goals)
    else:
        # GET branch: Prepopulate values from session or set defaults.
        try:
            if "annualRate" in session and session["annualRate"]:
                income = float(session["annualRate"]) / 12
            else:
                income = 0
        except ValueError:
            income = 0

        # For expenses, if stored in session as a dictionary, extract the total.
        expenses_data = session.get("expenses", "")
        if isinstance(expenses_data, dict):
            try:
                expenses = float(expenses_data.get("total", 0))
            except ValueError:
                expenses = 0
        else:
            try:
                expenses = float(expenses_data)
            except (ValueError, TypeError):
                expenses = 0

        try:
            if "workHours" in session and session["workHours"]:
                weekly_hours = float(session["workHours"])
            else:
                weekly_hours = 0
        except ValueError:
            weekly_hours = 0

        # Compute discretionary income based on these defaults.
        discretionary_income = income - expenses
        monthly_hours = weekly_hours * 4.33 if weekly_hours else 1
        hourly_value = discretionary_income / monthly_hours

        # For GET, if no savings values are provided, default them to zero.
        savings_goal = 0
        current_savings = 0
        remaining_to_save = 0
        progress_percent = 0

        # Retrieve long-term goals.
        goals = session.get("goals", [])

        return render_template("budget.html",
                               income=income,
                               expenses=expenses,
                               weekly_hours=weekly_hours,
                               discretionary_income=discretionary_income,
                               hourly_value=hourly_value,
                               savings_goal=savings_goal,
                               current_savings=current_savings,
                               remaining_to_save=remaining_to_save,
                               progress_percent=progress_percent,
                               goals=goals)


@app.route("/goals", methods=["GET", "POST"])
def goals():
    # Retrieve existing goals from the session.
    # We store goals as a list of dictionaries.
    existing_goals = session.get("goals", [])
    if not isinstance(existing_goals, list):
        existing_goals = []

    if request.method == "POST":
        # Determine whether the form submission is for adding a new goal or updating an existing goal.
        if "new_goal" in request.form:
            # Adding a new goal.
            goal_name = request.form.get("goal_name")
            target_amount = request.form.get("target_amount", "0")
            current_savings = request.form.get("current_savings", "0")
            try:
                target_amount = float(target_amount)
                current_savings = float(current_savings)
            except ValueError:
                target_amount = 0.0
                current_savings = 0.0
            new_goal = {"name": goal_name, "target": target_amount, "current": current_savings}
            existing_goals.append(new_goal)
            session["goals"] = existing_goals

        elif "update_goal" in request.form:
            # Updating (adding savings to) an existing goal.
            try:
                index = int(request.form.get("goal_index"))
                add_amount = float(request.form.get("savings_to_add", "0"))
            except ValueError:
                index = -1
                add_amount = 0.0
            if 0 <= index < len(existing_goals):
                existing_goals[index]["current"] += add_amount
                session["goals"] = existing_goals

        return redirect(url_for("goals"))

    return render_template("goals.html", goals=existing_goals)


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
