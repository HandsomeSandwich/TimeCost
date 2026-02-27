"""
Couples: Making Invisible Work Visible
=======================================
Self-log household labour. No hierarchy. No approval. Full equality.
"""

import csv
import hashlib
import io
import secrets
from datetime import date, datetime, timedelta

from flask import redirect, render_template, request, session, url_for, Response
from sqlalchemy import text

from . import couples_bp
from database import engine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COUPLES_CATEGORIES = [
    "Childcare",
    "Cooking & Meals",
    "Cleaning",
    "Laundry",
    "Errands & Shopping",
    "Home Maintenance",
    "Admin & Bills",
    "Emotional Labor & Planning",
    "Pet Care",
    "Other",
]

DEFAULT_TASKS = [
    ("Cook dinner", "Cooking & Meals", 45),
    ("Cook lunch", "Cooking & Meals", 30),
    ("Prep packed lunches", "Cooking & Meals", 15),
    ("Wash dishes / load dishwasher", "Cleaning", 15),
    ("Hoover / vacuum", "Cleaning", 30),
    ("Clean bathroom", "Cleaning", 25),
    ("Tidy kitchen", "Cleaning", 15),
    ("Do a load of laundry", "Laundry", 15),
    ("Fold and put away laundry", "Laundry", 20),
    ("Iron clothes", "Laundry", 30),
    ("Weekly food shop", "Errands & Shopping", 60),
    ("Quick top-up shop", "Errands & Shopping", 20),
    ("School run (drop-off)", "Childcare", 30),
    ("School run (pick-up)", "Childcare", 30),
    ("Bedtime routine", "Childcare", 30),
    ("Help with homework", "Childcare", 30),
    ("Take bins out", "Home Maintenance", 5),
    ("Mow the lawn", "Home Maintenance", 45),
    ("Pay bills / admin", "Admin & Bills", 20),
    ("Plan meals for the week", "Emotional Labor & Planning", 20),
    ("Book appointments", "Emotional Labor & Planning", 15),
    ("Walk the dog", "Pet Care", 30),
    ("Feed pets", "Pet Care", 10),
]

# ---------------------------------------------------------------------------
# Helpers (PIN auth copied locally to avoid circular imports)
# ---------------------------------------------------------------------------

def _pin_hash(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + pin).encode()).hexdigest()

def _make_pin(pin: str):
    salt = secrets.token_hex(8)
    return _pin_hash(pin, salt), salt

def _verify_pin(pin: str, pin_hash: str, salt: str) -> bool:
    return _pin_hash(pin, salt) == pin_hash

def _couples_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")

def _couples_today() -> str:
    return date.today().isoformat()

def _couples_make_code() -> str:
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(chars) for _ in range(6))

def _couples_require_partner() -> int:
    pid = session.get("couples_partner_id")
    return int(pid) if pid else 0

def _couples_partnership_id(partner_id: int) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT partnership_id FROM couples_partners WHERE id = :id"),
            {"id": partner_id},
        ).mappings().first()
        return int(row["partnership_id"]) if row else 0

def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def _get_connection():
    return engine.connect()

# ---------------------------------------------------------------------------
# Phase 1: Landing, Setup, Join, Login, Logout
# ---------------------------------------------------------------------------

@couples_bp.get("/")
def couples_landing():
    if _couples_require_partner():
        return redirect(url_for("couples.couples_dashboard"))
    return render_template("couples_landing.html")


@couples_bp.route("/setup", methods=["GET", "POST"])
def couples_setup():
    if request.method == "POST":
        p_name = (request.form.get("partnership_name") or "").strip()
        name = (request.form.get("partner_name") or "").strip()
        pin = (request.form.get("pin") or "").strip()
        pin_confirm = (request.form.get("pin_confirm") or "").strip()

        if not name or not pin:
            return render_template("couples_setup.html", error="Name and PIN are required.")
        if pin != pin_confirm:
            return render_template("couples_setup.html", error="PINs don't match.")

        ph, ps = _make_pin(pin)
        code = _couples_make_code()
        now = _couples_now()

        with engine.begin() as conn:
            row = conn.execute(
                text("""INSERT INTO couples_partnerships (name, partnership_code, created_at)
                        VALUES (:name, :code, :now) RETURNING id"""),
                {"name": p_name or None, "code": code, "now": now},
            ).mappings().first()
            pid = row["id"] if row else None
            if pid is None:
                pid = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

            row2 = conn.execute(
                text("""INSERT INTO couples_partners (partnership_id, name, pin_hash, pin_salt, created_at)
                        VALUES (:pid, :name, :ph, :ps, :now) RETURNING id"""),
                {"pid": pid, "name": name, "ph": ph, "ps": ps, "now": now},
            ).mappings().first()
            partner_id = row2["id"] if row2 else None
            if partner_id is None:
                partner_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

            # Seed default tasks
            for title, cat, mins in DEFAULT_TASKS:
                conn.execute(
                    text("""INSERT INTO couples_tasks (partnership_id, title, category, default_minutes, created_by, created_at)
                            VALUES (:pid, :t, :c, :m, :cb, :now)"""),
                    {"pid": pid, "t": title, "c": cat, "m": mins, "cb": partner_id, "now": now},
                )

        session["couples_partner_id"] = int(partner_id)
        session["couples_partnership_code"] = code
        return redirect(url_for("couples.couples_dashboard"))

    return render_template("couples_setup.html")


@couples_bp.route("/join", methods=["GET", "POST"])
def couples_join():
    if request.method == "POST":
        code = (request.form.get("partnership_code") or "").strip().upper()
        name = (request.form.get("partner_name") or "").strip()
        pin = (request.form.get("pin") or "").strip()
        pin_confirm = (request.form.get("pin_confirm") or "").strip()

        if not code or not name or not pin:
            return render_template("couples_join.html", error="All fields are required.")
        if pin != pin_confirm:
            return render_template("couples_join.html", error="PINs don't match.")

        with engine.begin() as conn:
            partnership = conn.execute(
                text("SELECT id FROM couples_partnerships WHERE partnership_code = :c"),
                {"c": code},
            ).mappings().first()
            if not partnership:
                return render_template("couples_join.html", error="Partnership code not found.")

            count = conn.execute(
                text("SELECT COUNT(*) AS c FROM couples_partners WHERE partnership_id = :pid"),
                {"pid": partnership["id"]},
            ).mappings().first()["c"]
            if count >= 2:
                return render_template("couples_join.html", error="This partnership already has two partners.")

            ph, ps = _make_pin(pin)
            now = _couples_now()

            row = conn.execute(
                text("""INSERT INTO couples_partners (partnership_id, name, pin_hash, pin_salt, created_at)
                        VALUES (:pid, :name, :ph, :ps, :now) RETURNING id"""),
                {"pid": partnership["id"], "name": name, "ph": ph, "ps": ps, "now": now},
            ).mappings().first()
            partner_id = row["id"] if row else None
            if partner_id is None:
                partner_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

        session["couples_partner_id"] = int(partner_id)
        session["couples_partnership_code"] = code
        return redirect(url_for("couples.couples_dashboard"))

    return render_template("couples_join.html")


@couples_bp.route("/login", methods=["GET", "POST"])
def couples_login():
    partnership_code = session.get("couples_partnership_code")

    if request.method == "POST":
        action = request.form.get("action")

        if action == "find_partnership":
            code = (request.form.get("partnership_code") or "").strip().upper()
            with engine.connect() as conn:
                p = conn.execute(
                    text("SELECT id FROM couples_partnerships WHERE partnership_code = :c"),
                    {"c": code},
                ).mappings().first()
                if p:
                    session["couples_partnership_code"] = code
                    return redirect(url_for("couples.couples_login"))
                else:
                    return render_template("couples_login.html", error="Partnership code not found.")

        elif action == "login":
            pin = (request.form.get("pin") or "").strip()
            partner_id = request.form.get("partner_id")

            if not partnership_code:
                return redirect(url_for("couples.couples_login"))
            if not partner_id or not pin:
                # Re-fetch partners for the form
                with engine.connect() as conn:
                    partners = conn.execute(
                        text("""SELECT cp.id, cp.name FROM couples_partners cp
                                JOIN couples_partnerships p ON p.id = cp.partnership_id
                                WHERE p.partnership_code = :c ORDER BY cp.name"""),
                        {"c": partnership_code},
                    ).mappings().all()
                return render_template("couples_login.html",
                                       partnership_code=partnership_code, partners=partners,
                                       error="Select your name and enter your PIN.")

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT id, pin_hash, pin_salt FROM couples_partners WHERE id = :id"),
                    {"id": partner_id},
                ).mappings().first()

            if not row or not _verify_pin(pin, row["pin_hash"], row["pin_salt"]):
                with engine.connect() as conn:
                    partners = conn.execute(
                        text("""SELECT cp.id, cp.name FROM couples_partners cp
                                JOIN couples_partnerships p ON p.id = cp.partnership_id
                                WHERE p.partnership_code = :c ORDER BY cp.name"""),
                        {"c": partnership_code},
                    ).mappings().all()
                return render_template("couples_login.html",
                                       partnership_code=partnership_code, partners=partners,
                                       error="Wrong PIN.")

            session["couples_partner_id"] = int(row["id"])
            return redirect(url_for("couples.couples_dashboard"))

    # GET
    if partnership_code:
        with engine.connect() as conn:
            partners = conn.execute(
                text("""SELECT cp.id, cp.name FROM couples_partners cp
                        JOIN couples_partnerships p ON p.id = cp.partnership_id
                        WHERE p.partnership_code = :c ORDER BY cp.name"""),
                {"c": partnership_code},
            ).mappings().all()
        return render_template("couples_login.html",
                               partnership_code=partnership_code, partners=partners)

    return render_template("couples_login.html")


@couples_bp.post("/logout")
def couples_logout():
    session.pop("couples_partner_id", None)
    session.pop("couples_partnership_code", None)
    return redirect(url_for("couples.couples_landing"))


# ---------------------------------------------------------------------------
# Phase 2: Task CRUD + Work Logging
# ---------------------------------------------------------------------------

@couples_bp.post("/task/add")
def couples_add_task():
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))
    pid = _couples_partnership_id(partner_id)

    title = (request.form.get("task_title") or "").strip()
    category = request.form.get("category", "Other")
    minutes = _safe_int(request.form.get("default_minutes"), 30)

    if not title:
        return redirect(url_for("couples.couples_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO couples_tasks (partnership_id, title, category, default_minutes, created_by, created_at)
                    VALUES (:pid, :t, :c, :m, :cb, :now)"""),
            {"pid": pid, "t": title, "c": category, "m": minutes, "cb": partner_id, "now": _couples_now()},
        )
    return redirect(url_for("couples.couples_dashboard"))


@couples_bp.post("/task/<int:task_id>/edit")
def couples_edit_task(task_id):
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))
    pid = _couples_partnership_id(partner_id)

    title = (request.form.get("task_title") or "").strip()
    category = request.form.get("category", "Other")
    minutes = _safe_int(request.form.get("default_minutes"), 30)

    if title:
        with engine.begin() as conn:
            conn.execute(
                text("""UPDATE couples_tasks SET title = :t, category = :c, default_minutes = :m
                        WHERE id = :tid AND partnership_id = :pid"""),
                {"t": title, "c": category, "m": minutes, "tid": task_id, "pid": pid},
            )
    return redirect(url_for("couples.couples_dashboard"))


@couples_bp.post("/task/<int:task_id>/delete")
def couples_delete_task(task_id):
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))
    pid = _couples_partnership_id(partner_id)

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE couples_tasks SET active = 0 WHERE id = :tid AND partnership_id = :pid"),
            {"tid": task_id, "pid": pid},
        )
    return redirect(url_for("couples.couples_dashboard"))


@couples_bp.post("/log")
def couples_log_work():
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))
    pid = _couples_partnership_id(partner_id)

    task_id = _safe_int(request.form.get("task_id"), 0) or None
    custom_title = (request.form.get("custom_title") or "").strip() or None
    category = request.form.get("category", "Other")
    minutes = _safe_int(request.form.get("minutes"), 0)
    work_date = request.form.get("work_date") or _couples_today()
    note = (request.form.get("note") or "").strip() or None

    if minutes <= 0:
        return redirect(url_for("couples.couples_dashboard"))

    # If task_id provided, pull category from the task
    if task_id:
        with engine.connect() as conn:
            task = conn.execute(
                text("SELECT category FROM couples_tasks WHERE id = :tid AND partnership_id = :pid"),
                {"tid": task_id, "pid": pid},
            ).mappings().first()
            if task:
                category = task["category"]

    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO couples_logs
                    (partnership_id, partner_id, task_id, custom_title, category, minutes, work_date, note, created_at)
                    VALUES (:pid, :partner, :tid, :ct, :cat, :min, :wd, :note, :now)"""),
            {"pid": pid, "partner": partner_id, "tid": task_id, "ct": custom_title,
             "cat": category, "min": minutes, "wd": work_date, "note": note, "now": _couples_now()},
        )
    return redirect(url_for("couples.couples_dashboard"))


@couples_bp.post("/log/<int:log_id>/edit")
def couples_edit_log(log_id):
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))

    minutes = _safe_int(request.form.get("minutes"), 0)
    note = (request.form.get("note") or "").strip() or None
    work_date = request.form.get("work_date")

    if minutes > 0:
        with engine.begin() as conn:
            # Own logs only — partner_id enforced
            conn.execute(
                text("""UPDATE couples_logs SET minutes = :m, note = :n, work_date = :wd
                        WHERE id = :lid AND partner_id = :partner"""),
                {"m": minutes, "n": note, "wd": work_date, "lid": log_id, "partner": partner_id},
            )
    return redirect(url_for("couples.couples_dashboard"))


@couples_bp.post("/log/<int:log_id>/delete")
def couples_delete_log(log_id):
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))

    with engine.begin() as conn:
        # Own logs only — partner_id enforced
        conn.execute(
            text("DELETE FROM couples_logs WHERE id = :lid AND partner_id = :partner"),
            {"lid": log_id, "partner": partner_id},
        )
    return redirect(url_for("couples.couples_dashboard"))


# ---------------------------------------------------------------------------
# Phase 3: Dashboard with Insights
# ---------------------------------------------------------------------------

def _couples_compute_insights(partnership_id, period="this_week"):
    """Compute dashboard data for a partnership."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    if period == "last_week":
        end = monday - timedelta(days=1)
        start = end - timedelta(days=6)
    elif period == "this_month":
        start = today.replace(day=1)
        end = today
    elif period == "all_time":
        start = date(2000, 1, 1)
        end = today
    else:  # this_week
        start = monday
        end = monday + timedelta(days=6)

    start_str = start.isoformat()
    end_str = end.isoformat()

    conn = _get_connection()
    try:
        # Per-partner totals
        totals = conn.execute(
            text("""SELECT partner_id, SUM(minutes) AS total_minutes
                    FROM couples_logs
                    WHERE partnership_id = :pid AND work_date >= :s AND work_date <= :e
                    GROUP BY partner_id"""),
            {"pid": partnership_id, "s": start_str, "e": end_str},
        ).mappings().all()
        partner_minutes = {r["partner_id"]: r["total_minutes"] or 0 for r in totals}

        # Category breakdown per partner
        cat_rows = conn.execute(
            text("""SELECT partner_id, category, SUM(minutes) AS mins
                    FROM couples_logs
                    WHERE partnership_id = :pid AND work_date >= :s AND work_date <= :e
                    GROUP BY partner_id, category
                    ORDER BY category"""),
            {"pid": partnership_id, "s": start_str, "e": end_str},
        ).mappings().all()

        # 7-day trend (always last 7 days regardless of period filter)
        trend_start = today - timedelta(days=6)
        trend_rows = conn.execute(
            text("""SELECT partner_id, work_date, SUM(minutes) AS mins
                    FROM couples_logs
                    WHERE partnership_id = :pid AND work_date >= :s AND work_date <= :e
                    GROUP BY partner_id, work_date
                    ORDER BY work_date"""),
            {"pid": partnership_id, "s": trend_start.isoformat(), "e": today.isoformat()},
        ).mappings().all()

        # Recent logs (last 30)
        recent = conn.execute(
            text("""SELECT l.id, l.partner_id, l.task_id, l.custom_title, l.category,
                           l.minutes, l.work_date, l.note, l.created_at,
                           p.name AS partner_name,
                           t.title AS task_title
                    FROM couples_logs l
                    JOIN couples_partners p ON p.id = l.partner_id
                    LEFT JOIN couples_tasks t ON t.id = l.task_id
                    WHERE l.partnership_id = :pid
                    ORDER BY l.work_date DESC, l.created_at DESC
                    LIMIT 30"""),
            {"pid": partnership_id},
        ).mappings().all()
    finally:
        conn.close()

    return {
        "partner_minutes": partner_minutes,
        "cat_rows": cat_rows,
        "trend_rows": trend_rows,
        "recent": recent,
        "start": start_str,
        "end": end_str,
        "period": period,
    }


@couples_bp.get("/dashboard")
def couples_dashboard():
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))

    partnership_id = _couples_partnership_id(partner_id)

    conn = _get_connection()
    try:
        partnership = conn.execute(
            text("SELECT * FROM couples_partnerships WHERE id = :id"),
            {"id": partnership_id},
        ).mappings().first()

        partners = conn.execute(
            text("SELECT * FROM couples_partners WHERE partnership_id = :pid ORDER BY id"),
            {"pid": partnership_id},
        ).mappings().all()

        tasks = conn.execute(
            text("""SELECT * FROM couples_tasks
                    WHERE partnership_id = :pid AND active = 1
                    ORDER BY category, title"""),
            {"pid": partnership_id},
        ).mappings().all()
    finally:
        conn.close()

    period = request.args.get("period", "this_week")
    insights = _couples_compute_insights(partnership_id, period)

    # Build per-partner data
    partner_a = partners[0] if len(partners) > 0 else None
    partner_b = partners[1] if len(partners) > 1 else None

    a_id = partner_a["id"] if partner_a else 0
    b_id = partner_b["id"] if partner_b else 0
    a_mins = insights["partner_minutes"].get(a_id, 0)
    b_mins = insights["partner_minutes"].get(b_id, 0)
    total_mins = a_mins + b_mins
    rate = float(partnership["hourly_rate"]) if partnership else 13.0
    currency = partnership["currency"] if partnership else "£"

    a_pct = round(a_mins / total_mins * 100) if total_mins > 0 else 50
    b_pct = 100 - a_pct

    # Category breakdown
    cat_data = {}
    for r in insights["cat_rows"]:
        cat = r["category"]
        if cat not in cat_data:
            cat_data[cat] = {"a": 0, "b": 0}
        if r["partner_id"] == a_id:
            cat_data[cat]["a"] += r["mins"]
        else:
            cat_data[cat]["b"] += r["mins"]

    categories = []
    for cat in sorted(cat_data.keys()):
        d = cat_data[cat]
        categories.append({
            "name": cat,
            "a_mins": d["a"],
            "b_mins": d["b"],
            "a_hours": round(d["a"] / 60, 1),
            "b_hours": round(d["b"] / 60, 1),
        })

    # 7-day chart data
    today = date.today()
    labels = [(today - timedelta(days=6 - i)).isoformat() for i in range(7)]
    trend_map = {}
    for r in insights["trend_rows"]:
        key = (r["partner_id"], r["work_date"])
        trend_map[key] = r["mins"]

    chart_data = {
        "labels": labels,
        "datasets": [],
    }
    if partner_a:
        chart_data["datasets"].append({
            "label": partner_a["name"],
            "data": [round(trend_map.get((a_id, d), 0) / 60, 1) for d in labels],
        })
    if partner_b:
        chart_data["datasets"].append({
            "label": partner_b["name"],
            "data": [round(trend_map.get((b_id, d), 0) / 60, 1) for d in labels],
        })

    return render_template(
        "couples_dashboard.html",
        partnership=partnership,
        partners=partners,
        partner_a=partner_a,
        partner_b=partner_b,
        partner_id=partner_id,
        tasks=tasks,
        categories_list=COUPLES_CATEGORIES,
        # Insights
        a_mins=a_mins, b_mins=b_mins, total_mins=total_mins,
        a_pct=a_pct, b_pct=b_pct,
        a_hours=round(a_mins / 60, 1), b_hours=round(b_mins / 60, 1),
        total_hours=round(total_mins / 60, 1),
        total_value=round(total_mins / 60 * rate, 2),
        a_value=round(a_mins / 60 * rate, 2),
        b_value=round(b_mins / 60 * rate, 2),
        rate=rate,
        currency=currency,
        categories=categories,
        recent=insights["recent"],
        chart_data=chart_data,
        period=insights["period"],
        today=_couples_today(),
    )


# ---------------------------------------------------------------------------
# Phase 4: Settings + Export
# ---------------------------------------------------------------------------

@couples_bp.post("/settings")
def couples_settings():
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))
    pid = _couples_partnership_id(partner_id)

    name = (request.form.get("partnership_name") or "").strip()
    rate = _safe_float(request.form.get("hourly_rate"), 13.0)
    currency = (request.form.get("currency") or "£").strip()

    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE couples_partnerships
                    SET name = :n, hourly_rate = :r, currency = :c
                    WHERE id = :pid"""),
            {"n": name or None, "r": rate, "c": currency, "pid": pid},
        )
    return redirect(url_for("couples.couples_dashboard"))


@couples_bp.get("/export")
def couples_export():
    partner_id = _couples_require_partner()
    if not partner_id:
        return redirect(url_for("couples.couples_login"))
    pid = _couples_partnership_id(partner_id)

    conn = _get_connection()
    try:
        partnership = conn.execute(
            text("SELECT hourly_rate, currency FROM couples_partnerships WHERE id = :id"),
            {"id": pid},
        ).mappings().first()
        rate = float(partnership["hourly_rate"]) if partnership else 13.0

        rows = conn.execute(
            text("""SELECT l.work_date, p.name AS partner_name,
                           COALESCE(t.title, l.custom_title, 'Custom') AS task,
                           l.category, l.minutes, l.note
                    FROM couples_logs l
                    JOIN couples_partners p ON p.id = l.partner_id
                    LEFT JOIN couples_tasks t ON t.id = l.task_id
                    WHERE l.partnership_id = :pid
                    ORDER BY l.work_date DESC, l.created_at DESC"""),
            {"pid": pid},
        ).mappings().all()
    finally:
        conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Partner", "Task", "Category", "Minutes", "Hours", "Value", "Note"])
    for r in rows:
        hrs = round(r["minutes"] / 60, 2)
        val = round(hrs * rate, 2)
        writer.writerow([r["work_date"], r["partner_name"], r["task"],
                         r["category"], r["minutes"], hrs, val, r["note"] or ""])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=invisible-work-export.csv"},
    )
