from __future__ import annotations

import secrets
import csv
import io
from datetime import date, datetime, timedelta
from typing import Optional

from flask import render_template, request, session, redirect, url_for, Response, jsonify
from sqlalchemy import text

from dinaro.db import engine, get_db_connection as get_connection
from dinaro.push import notify_parents, notify_child
from dinaro.kernel import (
    safe_float,
    make_pin as _make_pin,
    verify_pin as _verify_pin,
    utc_now_iso as _dinaro_now,
)
from . import dinaro_bp


# ----------------------------
# Dinaro Helpers
# (safe_float / pin / timestamp helpers now live in the core package)
# ----------------------------

def _dinaro_rate_for_family(family_id: int) -> float:
    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT rate_per_hour FROM dinaro_families WHERE id = :id"),
            {"id": family_id},
        ).mappings().first()
        return float(row["rate_per_hour"]) if row else 4.0
    finally:
        conn.close()


def _dinaro_make_family_code() -> str:
    """Generate a unique 6-char code for families."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No I, O, 0, 1
    code = "".join(secrets.choice(chars) for _ in range(6))
    return code


def _dinaro_ensure_family_codes():
    """Fill in missing family codes."""
    conn = get_connection()
    try:
        rows = conn.execute(
            text("SELECT id FROM dinaro_families WHERE family_code IS NULL")
        ).mappings().all()
        if not rows:
            return
        
        with engine.begin() as conn2:
            for r in rows:
                code = _dinaro_make_family_code()
                conn2.execute(
                    text("UPDATE dinaro_families SET family_code = :code WHERE id = :id"),
                    {"code": code, "id": r["id"]}
                )
    finally:
        conn.close()


def _dinaro_add_ledger(child_id: int, delta: float, reason: str, request_id=None, log_id=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dinaro_ledger (child_id, delta, reason, created_at, request_id, log_id)
                VALUES (:child_id, :delta, :reason, :created_at, :request_id, :log_id)
                """
            ),
            {
                "child_id": child_id,
                "delta": delta,
                "reason": reason,
                "created_at": _dinaro_now(),
                "request_id": request_id,
                "log_id": log_id,
            },
        )
        conn.execute(
            text("UPDATE dinaro_children SET balance = balance + :delta WHERE id = :id"),
            {"delta": delta, "id": child_id},
        )


def _dinaro_require_parent() -> int:
    parent_id = session.get("dinaro_parent_id")
    if not parent_id:
        return 0
    return int(parent_id)


def _dinaro_require_child() -> int:
    child_id = session.get("dinaro_child_id")
    if not child_id:
        return 0
    return int(child_id)


def _dinaro_process_financials(child_id: int):
    """Calculate and apply interest and taxes for a child if they are due (once per day)."""
    conn = get_connection()
    try:
        child = conn.execute(
            text("SELECT id, family_id, balance, last_interest_at, last_tax_at FROM dinaro_children WHERE id = :id"),
            {"id": child_id}
        ).mappings().first()
        if not child:
            return

        family = conn.execute(
            text("SELECT interest_rate, interest_threshold, tax_rate FROM dinaro_families WHERE id = :id"),
            {"id": child["family_id"]}
        ).mappings().first()
        if not family:
            return

        today = date.today().isoformat()
        balance = float(child["balance"] or 0)

        # 1. Interest (Savings Bonus)
        interest_rate = float(family["interest_rate"] or 0)
        threshold = float(family["interest_threshold"] or 0)
        last_interest = child["last_interest_at"]

        if interest_rate > 0 and balance >= threshold and last_interest != today:
            bonus = round(balance * (interest_rate / 100.0), 2)
            if bonus > 0:
                _dinaro_add_ledger(child_id, bonus, f"🏦 Savings Bonus ({interest_rate}%)")
                with engine.begin() as conn_upd:
                    conn_upd.execute(
                        text("UPDATE dinaro_children SET last_interest_at = :today WHERE id = :id"),
                        {"today": today, "id": child_id}
                    )

        # 2. Tax (Subscription)
        tax_rate = float(family["tax_rate"] or 0)
        last_tax = child["last_tax_at"]

        if last_tax != today:
            # Global Family Tax
            if tax_rate > 0 and balance > 0:
                tax_amount = round(balance * (tax_rate / 100.0), 2)
                if tax_amount > 0:
                    _dinaro_add_ledger(child_id, -tax_amount, f"💸 Parent Tax ({tax_rate}%)")

            # Recurring Expenses (Subscriptions)
            recurring_expenses = conn.execute(
                text("SELECT title, default_hours FROM dinaro_chores WHERE family_id = :fid AND chore_type = 'expense' AND recurrence = 'daily' AND active = 1"),
                {"fid": child["family_id"]}
            ).mappings().all()

            # For weekly, we'd need more complex logic (last_weekly_tax_at), 
            # but for now, we'll support daily recurring expenses easily.
            family_rate = _dinaro_rate_for_family(child["family_id"])
            for re in recurring_expenses:
                cost = round(float(re["default_hours"]) * family_rate, 2)
                if cost > 0:
                    _dinaro_add_ledger(child_id, -cost, f"📉 Recurring: {re['title']}")

            with engine.begin() as conn_upd:
                conn_upd.execute(
                    text("UPDATE dinaro_children SET last_tax_at = :today WHERE id = :id"),
                    {"today": today, "id": child_id}
                )
    finally:
        conn.close()


def _dinaro_parent_family_id(parent_id: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT family_id FROM dinaro_parents WHERE id = :id"),
            {"id": parent_id},
        ).mappings().first()
        return int(row["family_id"]) if row else 0
    finally:
        conn.close()


def _dinaro_child_family_id(child_id: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT family_id FROM dinaro_children WHERE id = :id"),
            {"id": child_id},
        ).mappings().first()
        return int(row["family_id"]) if row else 0
    finally:
        conn.close()


def _dinaro_get_linked_families(parent_id: int) -> list:
    """Return other classes this teacher manages (via link_code)."""
    conn = get_connection()
    try:
        parent = conn.execute(
            text("SELECT link_code, family_id FROM dinaro_parents WHERE id = :id"),
            {"id": parent_id},
        ).mappings().first()
        if not parent or not parent["link_code"]:
            return []
        rows = conn.execute(
            text(
                "SELECT p.id AS parent_id, p.family_id, f.name AS class_name "
                "FROM dinaro_parents p JOIN dinaro_families f ON f.id = p.family_id "
                "WHERE p.link_code = :lc AND p.family_id != :current_fid ORDER BY f.name ASC"
            ),
            {"lc": parent["link_code"], "current_fid": parent["family_id"]},
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _dinaro_class_analytics(family_id: int) -> dict:
    """Return classroom analytics: leaderboard, avg_balance, tasks_today, completion_rate, num_students."""
    conn = get_connection()
    try:
        kids = conn.execute(
            text("SELECT id, name, balance FROM dinaro_children WHERE family_id = :fid AND approved = 1 ORDER BY balance DESC"),
            {"fid": family_id},
        ).mappings().all()

        if not kids:
            return {"leaderboard": [], "avg_balance": 0, "tasks_today": 0, "completion_rate": 0, "num_students": 0}

        today = date.today().isoformat()
        monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()

        tasks_today = conn.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM dinaro_chore_logs l
                JOIN dinaro_children c ON c.id = l.child_id
                WHERE c.family_id = :fid AND l.work_date = :today AND l.status = 'approved'
            """),
            {"fid": family_id, "today": today},
        ).mappings().first()["cnt"]

        recurring_chores = conn.execute(
            text("SELECT COUNT(*) AS cnt FROM dinaro_chores WHERE family_id = :fid AND active = 1 AND recurrence != 'none'"),
            {"fid": family_id},
        ).mappings().first()["cnt"]

        leaderboard = []
        for kid in kids:
            tasks_week = conn.execute(
                text("""
                    SELECT COUNT(*) AS cnt FROM dinaro_chore_logs
                    WHERE child_id = :cid AND work_date >= :monday AND status IN ('approved', 'pending')
                """),
                {"cid": kid["id"], "monday": monday},
            ).mappings().first()["cnt"]
            leaderboard.append({
                "id": kid["id"],
                "name": kid["name"],
                "balance": float(kid["balance"] or 0),
                "tasks_week": tasks_week,
            })

        total_possible = recurring_chores * len(kids)
        total_done_today = conn.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM dinaro_chore_logs l
                JOIN dinaro_children c ON c.id = l.child_id
                WHERE c.family_id = :fid AND l.work_date = :today AND l.status IN ('approved', 'pending')
            """),
            {"fid": family_id, "today": today},
        ).mappings().first()["cnt"]

        completion_rate = round((total_done_today / total_possible * 100), 1) if total_possible > 0 else 0
        avg_balance = round(sum(float(k["balance"] or 0) for k in kids) / len(kids), 2)

        return {
            "leaderboard": leaderboard,
            "avg_balance": avg_balance,
            "tasks_today": tasks_today,
            "completion_rate": completion_rate,
            "num_students": len(kids),
        }
    finally:
        conn.close()


def _dinaro_check_group_rewards(family_id: int) -> None:
    """Check and award group rewards after a chore is approved."""
    conn = get_connection()
    try:
        rewards = conn.execute(
            text("SELECT * FROM dinaro_group_rewards WHERE family_id = :fid AND active = 1"),
            {"fid": family_id},
        ).mappings().all()

        if not rewards:
            return

        kids = conn.execute(
            text("SELECT id, name FROM dinaro_children WHERE family_id = :fid AND approved = 1"),
            {"fid": family_id},
        ).mappings().all()

        if not kids:
            return

        today = date.today().isoformat()
        monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()

        for reward in rewards:
            period = reward["condition_period"]
            period_start = today if period == "daily" else monday

            if reward["last_awarded_at"] and reward["last_awarded_at"] >= period_start:
                continue

            met = False

            if reward["condition_type"] == "all_complete" and reward["condition_chore_id"]:
                all_done = True
                for kid in kids:
                    has_log = conn.execute(
                        text("""
                            SELECT COUNT(*) AS cnt FROM dinaro_chore_logs
                            WHERE child_id = :cid AND chore_id = :chid
                            AND work_date >= :start AND status = 'approved'
                        """),
                        {"cid": kid["id"], "chid": reward["condition_chore_id"], "start": period_start},
                    ).mappings().first()["cnt"]
                    if has_log == 0:
                        all_done = False
                        break
                met = all_done

            elif reward["condition_type"] == "class_target" and reward["condition_target"]:
                total = conn.execute(
                    text("""
                        SELECT COUNT(*) AS cnt FROM dinaro_chore_logs l
                        JOIN dinaro_children c ON c.id = l.child_id
                        WHERE c.family_id = :fid AND l.work_date >= :start AND l.status = 'approved'
                    """),
                    {"fid": family_id, "start": period_start},
                ).mappings().first()["cnt"]
                met = total >= int(reward["condition_target"])

            if met:
                reward_amount = float(reward["reward_dinaro"])
                for kid in kids:
                    _dinaro_add_ledger(int(kid["id"]), reward_amount, f"🏅 Group Reward: {reward['title']}")
                    notify_child(family_id, int(kid["id"]),
                                 "Group reward earned!",
                                 f"Your class earned '{reward['title']}'! +{reward_amount:.2f} dinaro")

                with engine.begin() as conn2:
                    conn2.execute(
                        text("UPDATE dinaro_group_rewards SET last_awarded_at = :today WHERE id = :id"),
                        {"today": today, "id": reward["id"]},
                    )
    finally:
        conn.close()


# ----------------------------
# Dinaro Routes
# ----------------------------

@dinaro_bp.get("/")
def dinaro_landing():
    if session.get("dinaro_parent_id"):
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))
    if session.get("dinaro_child_id"):
        return redirect(url_for("dinaro.dinaro_child_dashboard"))
    return render_template("dinaro_landing.html")


@dinaro_bp.route("/setup", methods=["GET", "POST"])
def dinaro_setup():
    conn = get_connection()
    try:
        existing = conn.execute(text("SELECT COUNT(*) AS c FROM dinaro_parents")).mappings().first()
        if existing and existing["c"] > 0:
            return redirect(url_for("dinaro.dinaro_parent_login"))
    finally:
        conn.close()

    if request.method == "POST":
        family_name = (request.form.get("family_name") or "").strip()
        parent_name = (request.form.get("parent_name") or "").strip()
        pin = (request.form.get("parent_pin") or "").strip()
        pin_confirm = (request.form.get("parent_pin_confirm") or "").strip()
        is_classroom = 1 if request.form.get("is_classroom") == "on" else 0

        if not parent_name or not pin or pin != pin_confirm:
            return render_template("dinaro_setup.html", error="Please check name and matching PIN.")

        pin_hash, pin_salt = _make_pin(pin)

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO dinaro_families (name, rate_per_hour, family_code, is_classroom) "
                    "VALUES (:name, :rate, :code, :ic) RETURNING id"
                ),
                {"name": family_name or None, "rate": 4, "code": _dinaro_make_family_code(), "ic": is_classroom},
            ).mappings().first()
            family_id = row["id"] if row else None
            if family_id is None:
                family_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]
            conn.execute(
                text(
                    "INSERT INTO dinaro_parents (family_id, name, pin_hash, pin_salt) "
                    "VALUES (:family_id, :name, :pin_hash, :pin_salt)"
                ),
                {
                    "family_id": family_id,
                    "name": parent_name,
                    "pin_hash": pin_hash,
                    "pin_salt": pin_salt,
                },
            )

        return redirect(url_for("dinaro.dinaro_parent_login"))

    return render_template("dinaro_setup.html")


@dinaro_bp.route("/parent/login", methods=["GET", "POST"])
def dinaro_parent_login():
    conn = get_connection()
    try:
        parents = conn.execute(
            text("SELECT id, name FROM dinaro_parents ORDER BY name ASC")
        ).mappings().all()
    finally:
        conn.close()

    if not parents:
        return redirect(url_for("dinaro.dinaro_setup"))

    if request.method == "POST":
        parent_id = request.form.get("parent_id")
        pin = (request.form.get("parent_pin") or "").strip()
        if not parent_id or not pin:
            return render_template("dinaro_parent_login.html", parents=parents, error="Enter your PIN.")

        conn = get_connection()
        try:
            row = conn.execute(
                text("SELECT id, pin_hash, pin_salt FROM dinaro_parents WHERE id = :id"),
                {"id": parent_id},
            ).mappings().first()
        finally:
            conn.close()

        if not row or not _verify_pin(pin, row["pin_hash"], row["pin_salt"]):
            return render_template("dinaro_parent_login.html", parents=parents, error="Wrong PIN.")

        session["dinaro_parent_id"] = int(row["id"])
        session.pop("dinaro_child_id", None)
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    return render_template("dinaro_parent_login.html", parents=parents)


@dinaro_bp.post("/parent/logout")
def dinaro_parent_logout():
    session.pop("dinaro_parent_id", None)
    return redirect(url_for("dinaro.dinaro_landing"))


def _dinaro_active_fund(family_id: int):
    """The family's most recent Treasury fund, or None."""
    conn = get_connection()
    try:
        return conn.execute(
            text("SELECT * FROM dinaro_class_funds WHERE family_id = :fid ORDER BY id DESC LIMIT 1"),
            {"fid": family_id},
        ).mappings().first()
    finally:
        conn.close()


@dinaro_bp.get("/parent")
def dinaro_parent_dashboard():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    conn = get_connection()
    try:
        family = conn.execute(
            text("SELECT id, name, rate_per_hour, family_code, is_classroom, interest_rate, interest_threshold, tax_rate, show_leaderboard, grade_mode FROM dinaro_families WHERE id = :id"),
            {"id": family_id},
        ).mappings().first()
        kids = conn.execute(
            text("SELECT id, name, balance, view_mode FROM dinaro_children WHERE family_id = :id AND approved = 1 ORDER BY name ASC"),
            {"id": family_id},
        ).mappings().all()
        pending_enrollments = conn.execute(
            text("SELECT id, name FROM dinaro_children WHERE family_id = :id AND approved = 0 ORDER BY name ASC"),
            {"id": family_id},
        ).mappings().all()
        parents = conn.execute(
            text("SELECT id, name FROM dinaro_parents WHERE family_id = :id ORDER BY name ASC"),
            {"id": family_id},
        ).mappings().all()
        chores = conn.execute(
            text(
                "SELECT id, title, default_hours, recurrence, chore_type FROM dinaro_chores "
                "WHERE family_id = :id AND active = 1 ORDER BY title ASC"
            ),
            {"id": family_id},
        ).mappings().all()
        spendables = conn.execute(
            text(
                "SELECT id, title, cost_dinaro FROM dinaro_spendables "
                "WHERE family_id = :id AND active = 1 ORDER BY title ASC"
            ),
            {"id": family_id},
        ).mappings().all()
        pending_logs = conn.execute(
            text(
                """
                SELECT l.id, l.child_id, l.chore_id, l.work_date, l.overtime_hours,
                       l.requested_hours, l.status, c.title AS chore_title, ch.name AS child_name
                FROM dinaro_chore_logs l
                LEFT JOIN dinaro_chores c ON c.id = l.chore_id
                LEFT JOIN dinaro_children ch ON ch.id = l.child_id
                WHERE l.status = 'pending' AND ch.family_id = :id
                ORDER BY l.created_at DESC
                """
            ),
            {"id": family_id},
        ).mappings().all()
        requests = conn.execute(
            text(
                """
                SELECT r.*, ch.name AS child_name
                FROM dinaro_requests r
                JOIN dinaro_children ch ON ch.id = r.child_id
                WHERE ch.family_id = :id
                ORDER BY r.created_at DESC
                """
            ),
            {"id": family_id},
        ).mappings().all()
        goals = conn.execute(
            text(
                """
                SELECT g.*, ch.name AS child_name, ch.balance
                FROM dinaro_goals g
                JOIN dinaro_children ch ON ch.id = g.child_id
                WHERE ch.family_id = :id
                ORDER BY g.id DESC
                """
            ),
            {"id": family_id},
        ).mappings().all()
        ledger = conn.execute(
            text(
                """
                SELECT l.*, ch.name AS child_name
                FROM dinaro_ledger l
                JOIN dinaro_children ch ON ch.id = l.child_id
                WHERE ch.family_id = :id
                ORDER BY l.created_at DESC LIMIT 100
                """
            ),
            {"id": family_id},
        ).mappings().all()
        group_rewards = conn.execute(
            text(
                """
                SELECT gr.*, c.title AS chore_title
                FROM dinaro_group_rewards gr
                LEFT JOIN dinaro_chores c ON c.id = gr.condition_chore_id
                WHERE gr.family_id = :id AND gr.active = 1
                ORDER BY gr.id DESC
                """
            ),
            {"id": family_id},
        ).mappings().all()

        # 7-day trend data
        today_dt = datetime.now()
        dates = [(today_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        dates.reverse()
        
        chart_data = {"labels": dates, "datasets": []}
        for kid in kids:
            balances = []
            curr_balance = float(kid["balance"] or 0)
            
            # Simplified: work backwards from current balance
            # For a more accurate chart, we'd need to sum deltas by date
            daily_deltas = {}
            for entry in ledger:
                if entry["child_id"] == kid["id"]:
                    edate = entry["created_at"][:10]
                    daily_deltas[edate] = daily_deltas.get(edate, 0) + float(entry["delta"])
            
            temp_balance = curr_balance
            day_balances = []
            for d in reversed(dates):
                day_balances.append(round(temp_balance, 2))
                temp_balance -= daily_deltas.get(d, 0)
            
            day_balances.reverse()
            chart_data["datasets"].append({
                "label": kid["name"],
                "data": day_balances
            })

    finally:
        conn.close()
    
    # Calculate To-Do Progress for each child
    today = date.today().isoformat()
    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    
    kids_with_progress = []
    for kid in kids:
        kid_dict = dict(kid)
        
        # Get recurring chores for the family
        recurring = [c for c in chores if c["recurrence"] in ("daily", "weekly")]
        
        # Get logs for this kid
        conn = get_connection()
        try:
            kid_logs = conn.execute(
                text("SELECT chore_id, work_date, status FROM dinaro_chore_logs WHERE child_id = :id AND work_date >= :monday"),
                {"id": kid["id"], "monday": (date.today() - timedelta(days=7)).isoformat()},
            ).mappings().all()
        finally:
            conn.close()
        
        done_count = 0
        total_count = len(recurring)
        
        for chore in recurring:
            if chore["recurrence"] == "daily":
                if any(l["chore_id"] == chore["id"] and l["work_date"] == today and l["status"] != 'denied' for l in kid_logs):
                    done_count += 1
            elif chore["recurrence"] == "weekly":
                if any(l["chore_id"] == chore["id"] and l["work_date"] >= monday and l["status"] != 'denied' for l in kid_logs):
                    done_count += 1
        
        kid_dict["todo_done"] = done_count
        kid_dict["todo_total"] = total_count
        kids_with_progress.append(kid_dict)

    # Classroom analytics
    analytics = _dinaro_class_analytics(family_id) if family and family["is_classroom"] else None

    treasury = _dinaro_active_fund(family_id)
    treasury_options = []
    treasury_bill_stats = {"paid": 0, "total": 0}
    if treasury:
        conn = get_connection()
        try:
            treasury_options = conn.execute(
                text(
                    "SELECT o.id, o.label, "
                    "(SELECT COUNT(*) FROM dinaro_fund_votes v WHERE v.option_id = o.id) AS votes "
                    "FROM dinaro_fund_options o WHERE o.fund_id = :fid ORDER BY o.id"
                ),
                {"fid": treasury["id"]},
            ).mappings().all()
            treasury_bill_stats = conn.execute(
                text(
                    "SELECT COUNT(*) AS total, "
                    "COALESCE(SUM(CASE WHEN amount_paid >= amount_owed THEN 1 ELSE 0 END), 0) AS paid "
                    "FROM dinaro_fund_bills WHERE fund_id = :fid"
                ),
                {"fid": treasury["id"]},
            ).mappings().first()
        finally:
            conn.close()

    return render_template(
        "dinaro_parent_dashboard.html",
        family=family,
        kids=kids_with_progress,
        parents=parents,
        chores=chores,
        spendables=spendables,
        pending_logs=pending_logs,
        requests=requests,
        goals=goals,
        ledger=ledger,
        chart_data=chart_data,
        rate_per_hour=family["rate_per_hour"] if family else 4,
        pending_enrollments=pending_enrollments,
        other_classes=_dinaro_get_linked_families(parent_id) if family and family["is_classroom"] else [],
        analytics=analytics,
        group_rewards=group_rewards,
        treasury=treasury,
        treasury_options=treasury_options,
        treasury_bill_stats=treasury_bill_stats,
    )


# ----------------------------
# The Treasury — teacher setup
# ----------------------------
@dinaro_bp.post("/parent/treasury/save")
def dinaro_parent_treasury_save():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))
    family_id = _dinaro_parent_family_id(parent_id)

    title = (request.form.get("title") or "The Treasury").strip() or "The Treasury"
    goal = safe_float(request.form.get("goal"), 0.0)
    match_num = int(safe_float(request.form.get("match_num"), 0.0))
    match_den = int(safe_float(request.form.get("match_den"), 1.0)) or 1
    tax_type = (request.form.get("tax_type") or "flat").strip()
    tax_type = tax_type if tax_type in ("flat", "percent") else "flat"
    tax_amount = safe_float(request.form.get("tax_amount"), 0.0)
    penalty_no_vote = 1 if request.form.get("penalty_no_vote") else 0
    penalty_interest = safe_float(request.form.get("penalty_interest"), 0.0)
    grade_mode = (request.form.get("grade_mode") or "score").strip()
    grade_mode = grade_mode if grade_mode in ("score", "bonus") else "score"

    existing = _dinaro_active_fund(family_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_families SET grade_mode = :gm WHERE id = :fid"),
            {"gm": grade_mode, "fid": family_id},
        )
        if existing:
            conn.execute(
                text(
                    "UPDATE dinaro_class_funds SET title=:t, goal=:g, match_num=:mn, match_den=:md, "
                    "tax_type=:tt, tax_amount=:ta, penalty_no_vote=:pnv, penalty_interest=:pi WHERE id=:id"
                ),
                {"t": title, "g": goal, "mn": match_num, "md": match_den, "tt": tax_type,
                 "ta": tax_amount, "pnv": penalty_no_vote, "pi": penalty_interest, "id": existing["id"]},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO dinaro_class_funds "
                    "(family_id, title, goal, match_num, match_den, tax_type, tax_amount, penalty_no_vote, penalty_interest, created_at) "
                    "VALUES (:fid, :t, :g, :mn, :md, :tt, :ta, :pnv, :pi, :now)"
                ),
                {"fid": family_id, "t": title, "g": goal, "mn": match_num, "md": match_den, "tt": tax_type,
                 "ta": tax_amount, "pnv": penalty_no_vote, "pi": penalty_interest, "now": _dinaro_now()},
            )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/treasury/option/add")
def dinaro_parent_treasury_option_add():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))
    fund = _dinaro_active_fund(_dinaro_parent_family_id(parent_id))
    label = (request.form.get("label") or "").strip()
    if fund and label:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO dinaro_fund_options (fund_id, label) VALUES (:fid, :l)"),
                {"fid": fund["id"], "l": label},
            )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/treasury/option/<int:option_id>/delete")
def dinaro_parent_treasury_option_delete(option_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))
    fund = _dinaro_active_fund(_dinaro_parent_family_id(parent_id))
    if fund:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM dinaro_fund_options WHERE id=:oid AND fund_id=:fid"),
                {"oid": option_id, "fid": fund["id"]},
            )
            conn.execute(text("DELETE FROM dinaro_fund_votes WHERE option_id=:oid"), {"oid": option_id})
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/treasury/bills")
def dinaro_parent_treasury_bills():
    """Issue (or refresh) each student's fair-share bill from the tax rule."""
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))
    family_id = _dinaro_parent_family_id(parent_id)
    fund = _dinaro_active_fund(family_id)
    if not fund:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        kids = conn.execute(
            text("SELECT id, balance FROM dinaro_children WHERE family_id=:fid AND approved=1"),
            {"fid": family_id},
        ).mappings().all()
        for kid in kids:
            if fund["tax_type"] == "percent":
                owed = round(float(kid["balance"]) * float(fund["tax_amount"]) / 100.0, 2)
            else:
                owed = round(float(fund["tax_amount"]), 2)
            existing_bill = conn.execute(
                text("SELECT id FROM dinaro_fund_bills WHERE fund_id=:fid AND child_id=:cid"),
                {"fid": fund["id"], "cid": kid["id"]},
            ).mappings().first()
            if existing_bill:
                conn.execute(
                    text("UPDATE dinaro_fund_bills SET amount_owed=:o WHERE id=:id"),
                    {"o": owed, "id": existing_bill["id"]},
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO dinaro_fund_bills (fund_id, child_id, amount_owed, amount_paid, created_at) "
                        "VALUES (:fid, :cid, :o, 0, :now)"
                    ),
                    {"fid": fund["id"], "cid": kid["id"], "o": owed, "now": _dinaro_now()},
                )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/treasury/open-vote")
def dinaro_parent_treasury_open_vote():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))
    fund = _dinaro_active_fund(_dinaro_parent_family_id(parent_id))
    if fund:
        with engine.begin() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM dinaro_fund_options WHERE fund_id=:f"), {"f": fund["id"]}).scalar()
            if n:
                conn.execute(text("UPDATE dinaro_class_funds SET status='voting' WHERE id=:f"), {"f": fund["id"]})
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/treasury/close-vote")
def dinaro_parent_treasury_close_vote():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))
    fund = _dinaro_active_fund(_dinaro_parent_family_id(parent_id))
    if fund:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE dinaro_class_funds SET status='closed', closed_at=:now WHERE id=:f"),
                {"now": _dinaro_now(), "f": fund["id"]},
            )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/settings")
def dinaro_parent_settings():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    name = (request.form.get("family_name") or "").strip() or None
    rate = safe_float(request.form.get("rate_per_hour"), 4.0)
    interest_rate = safe_float(request.form.get("interest_rate"), 0.0)
    interest_threshold = safe_float(request.form.get("interest_threshold"), 100.0)
    tax_rate = safe_float(request.form.get("tax_rate"), 0.0)
    is_classroom = 1 if request.form.get("is_classroom") == "on" else 0
    show_leaderboard = 1 if request.form.get("show_leaderboard") == "on" else 0

    if rate <= 0:
        rate = 4.0

    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE dinaro_families
                SET name = :name, rate_per_hour = :rate,
                    interest_rate = :ir, interest_threshold = :it, tax_rate = :tr,
                    is_classroom = :ic, show_leaderboard = :sl
                WHERE id = :id
            """),
            {
                "name": name, "rate": rate,
                "ir": interest_rate, "it": interest_threshold, "tr": tax_rate,
                "ic": is_classroom, "sl": show_leaderboard,
                "id": family_id
            },
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/class/create")
def dinaro_parent_create_class():
    """Create an additional class for a teacher (classroom mode only)."""
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    conn = get_connection()
    try:
        family = conn.execute(
            text("SELECT is_classroom FROM dinaro_families WHERE id = :id"),
            {"id": family_id},
        ).mappings().first()
        parent = conn.execute(
            text("SELECT name, pin_hash, pin_salt, link_code FROM dinaro_parents WHERE id = :id"),
            {"id": parent_id},
        ).mappings().first()
    finally:
        conn.close()

    if not family or not family["is_classroom"] or not parent:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    class_name = (request.form.get("class_name") or "").strip() or None

    # Generate link_code if the teacher doesn't have one yet
    link_code = parent["link_code"]
    if not link_code:
        link_code = _dinaro_make_family_code()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE dinaro_parents SET link_code = :lc WHERE id = :id"),
                {"lc": link_code, "id": parent_id},
            )

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO dinaro_families (name, rate_per_hour, family_code, is_classroom) "
                "VALUES (:name, :rate, :code, 1) RETURNING id"
            ),
            {"name": class_name, "rate": 4, "code": _dinaro_make_family_code()},
        ).mappings().first()
        new_family_id = row["id"] if row else None
        if new_family_id is None:
            new_family_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

        row2 = conn.execute(
            text(
                "INSERT INTO dinaro_parents (family_id, name, pin_hash, pin_salt, link_code) "
                "VALUES (:fid, :name, :hash, :salt, :lc) RETURNING id"
            ),
            {"fid": new_family_id, "name": parent["name"],
             "hash": parent["pin_hash"], "salt": parent["pin_salt"], "lc": link_code},
        ).mappings().first()
        new_parent_id = row2["id"] if row2 else None
        if new_parent_id is None:
            new_parent_id = conn.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]

    session["dinaro_parent_id"] = int(new_parent_id)
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/class/switch")
def dinaro_parent_switch_class():
    """Switch to another linked class."""
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    target_parent_id = request.form.get("target_parent_id")
    if not target_parent_id:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    conn = get_connection()
    try:
        current = conn.execute(
            text("SELECT link_code FROM dinaro_parents WHERE id = :id"),
            {"id": parent_id},
        ).mappings().first()
        target = conn.execute(
            text("SELECT id, link_code FROM dinaro_parents WHERE id = :id"),
            {"id": int(target_parent_id)},
        ).mappings().first()
    finally:
        conn.close()

    if not current or not target or not current["link_code"] or current["link_code"] != target["link_code"]:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    session["dinaro_parent_id"] = int(target["id"])
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.get("/parent/upgrade")
def dinaro_parent_upgrade():
    """Demand test: interest-capture page shown when a family hits the free
    child limit. No payment — reuses email_signups via core.subscribe."""
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    conn = get_connection()
    try:
        child_count = conn.execute(
            text("SELECT COUNT(*) AS c FROM dinaro_children WHERE family_id = :fid AND approved = 1"),
            {"fid": family_id},
        ).mappings().first()["c"]
    finally:
        conn.close()

    subscribed = request.args.get("subscribed") == "1"
    return render_template("dinaro_upgrade.html", child_count=child_count, subscribed=subscribed)


@dinaro_bp.post("/parent/child/add")
def dinaro_parent_add_child():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)

    # --- Demand test: free tier caps children; extra children capture interest ---
    conn = get_connection()
    try:
        existing_count = conn.execute(
            text("SELECT COUNT(*) AS c FROM dinaro_children WHERE family_id = :fid"),
            {"fid": family_id},
        ).mappings().first()["c"]
    finally:
        conn.close()

    FREE_CHILD_LIMIT = 2
    if existing_count >= FREE_CHILD_LIMIT:
        return redirect(url_for("dinaro.dinaro_parent_upgrade"))
    # --- end demand test ---

    name = (request.form.get("child_name") or "").strip()
    pin = (request.form.get("child_pin") or "").strip()
    if not name or not pin:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    pin_hash, pin_salt = _make_pin(pin)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dinaro_children (family_id, name, pin_hash, pin_salt, view_mode) "
                "VALUES (:family_id, :name, :pin_hash, :pin_salt, :mode)"
            ),
            {"family_id": family_id, "name": name, "pin_hash": pin_hash, "pin_salt": pin_salt, "mode": "visual"},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/child/<int:child_id>/edit")
def dinaro_parent_edit_child(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    name = (request.form.get("child_name") or "").strip()
    pin = (request.form.get("child_pin") or "").strip()

    if not name:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        if pin:
            pin_hash, pin_salt = _make_pin(pin)
            conn.execute(
                text(
                    "UPDATE dinaro_children SET name = :name, pin_hash = :pin_hash, pin_salt = :pin_salt "
                    "WHERE id = :id AND family_id = :family_id"
                ),
                {"name": name, "pin_hash": pin_hash, "pin_salt": pin_salt, "id": child_id, "family_id": family_id},
            )
        else:
            conn.execute(
                text(
                    "UPDATE dinaro_children SET name = :name "
                    "WHERE id = :id AND family_id = :family_id"
                ),
                {"name": name, "id": child_id, "family_id": family_id},
            )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/child/<int:child_id>/mode")
def dinaro_parent_update_child_mode(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    mode = (request.form.get("view_mode") or "visual").strip()

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_children SET view_mode = :mode "
                 "WHERE id = :id AND family_id = :family_id"),
            {"mode": mode, "id": child_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/child/<int:child_id>/delete")
def dinaro_parent_delete_child(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dinaro_children WHERE id = :id AND family_id = :family_id"),
            {"id": child_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/parent/add")
def dinaro_parent_add_parent():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    name = (request.form.get("parent_name") or "").strip()
    pin = (request.form.get("parent_pin") or "").strip()
    if not name or not pin:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    pin_hash, pin_salt = _make_pin(pin)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dinaro_parents (family_id, name, pin_hash, pin_salt) "
                "VALUES (:family_id, :name, :pin_hash, :pin_salt)"
            ),
            {"family_id": family_id, "name": name, "pin_hash": pin_hash, "pin_salt": pin_salt},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/parent/<int:parent_id>/edit")
def dinaro_parent_edit_parent(parent_id: int):
    p_id = _dinaro_require_parent()
    if not p_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(p_id)
    name = (request.form.get("parent_name") or "").strip()
    pin = (request.form.get("parent_pin") or "").strip()

    if not name:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        if pin:
            pin_hash, pin_salt = _make_pin(pin)
            conn.execute(
                text(
                    "UPDATE dinaro_parents SET name = :name, pin_hash = :pin_hash, pin_salt = :pin_salt "
                    "WHERE id = :id AND family_id = :family_id"
                ),
                {"name": name, "pin_hash": pin_hash, "pin_salt": pin_salt, "id": parent_id, "family_id": family_id},
            )
        else:
            conn.execute(
                text(
                    "UPDATE dinaro_parents SET name = :name "
                    "WHERE id = :id AND family_id = :family_id"
                ),
                {"name": name, "id": parent_id, "family_id": family_id},
            )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/parent/<int:parent_id>/delete")
def dinaro_parent_delete_parent(parent_id: int):
    p_id = _dinaro_require_parent()
    if not p_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    # Security check: cannot delete yourself
    if int(parent_id) == p_id:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    family_id = _dinaro_parent_family_id(p_id)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dinaro_parents WHERE id = :id AND family_id = :family_id"),
            {"id": parent_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/chore/add")
def dinaro_parent_add_chore():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("chore_title") or "").strip()
    hours = safe_float(request.form.get("default_hours"), 0.5)
    recurrence = request.form.get("recurrence") or "none"
    chore_type = (request.form.get("chore_type") or "income").strip()

    if not title:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dinaro_chores (family_id, title, default_hours, recurrence, chore_type) "
                "VALUES (:family_id, :title, :hours, :recurrence, :chore_type)"
            ),
            {"family_id": family_id, "title": title, "hours": hours, "recurrence": recurrence, "chore_type": chore_type},
        )

    # Broadcast to other classes if requested (classroom mode)
    broadcast_ids = request.form.getlist("broadcast_to_families")
    if broadcast_ids:
        linked = _dinaro_get_linked_families(parent_id)
        valid_fids = {f["family_id"] for f in linked}
        with engine.begin() as conn:
            for fid_str in broadcast_ids:
                fid = int(fid_str)
                if fid in valid_fids:
                    conn.execute(
                        text(
                            "INSERT INTO dinaro_chores (family_id, title, default_hours, recurrence, chore_type) "
                            "VALUES (:family_id, :title, :hours, :recurrence, :chore_type)"
                        ),
                        {"family_id": fid, "title": title, "hours": hours, "recurrence": recurrence, "chore_type": chore_type},
                    )

    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/chore/<int:chore_id>/edit")
def dinaro_parent_edit_chore(chore_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("chore_title") or "").strip()
    hours = safe_float(request.form.get("default_hours"), 0.5)
    recurrence = (request.form.get("recurrence") or "none").strip()
    chore_type = (request.form.get("chore_type") or "income").strip()

    if not title:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dinaro_chores SET title = :title, default_hours = :hours, "
                "recurrence = :recurrence, chore_type = :chore_type "
                "WHERE id = :id AND family_id = :family_id"
            ),
            {"title": title, "hours": hours, "recurrence": recurrence, "chore_type": chore_type, "id": chore_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/chore/<int:chore_id>/delete")
def dinaro_parent_delete_chore(chore_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_chores SET active = 0 WHERE id = :id AND family_id = :family_id"),
            {"id": chore_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/spendable/add")
def dinaro_parent_add_spendable():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("spendable_title") or "").strip()
    cost = safe_float(request.form.get("cost_dinaro"), 1.0)

    if not title:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dinaro_spendables (family_id, title, cost_dinaro) "
                "VALUES (:family_id, :title, :cost)"
            ),
            {"family_id": family_id, "title": title, "cost": cost},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/spendable/<int:spendable_id>/edit")
def dinaro_parent_edit_spendable(spendable_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("spendable_title") or "").strip()
    cost = safe_float(request.form.get("cost_dinaro"), 1.0)

    if not title:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dinaro_spendables SET title = :title, cost_dinaro = :cost "
                "WHERE id = :id AND family_id = :family_id"
            ),
            {"title": title, "cost": cost, "id": spendable_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/spendable/<int:spendable_id>/delete")
def dinaro_parent_delete_spendable(spendable_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_spendables SET active = 0 WHERE id = :id AND family_id = :family_id"),
            {"id": spendable_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/log/<int:log_id>/approve")
def dinaro_parent_approve_log(log_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    approved_hours = safe_float(request.form.get("approved_hours"), 0.0)
    conn = get_connection()
    try:
        row = conn.execute(
            text(
                """
                SELECT l.child_id, l.requested_hours, ch.family_id
                FROM dinaro_chore_logs l
                JOIN dinaro_children ch ON ch.id = l.child_id
                WHERE l.id = :id
                """
            ),
            {"id": log_id},
        ).mappings().first()
    finally:
        conn.close()

    if not row:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    if approved_hours <= 0:
        approved_hours = float(row["requested_hours"])

    rate = _dinaro_rate_for_family(int(row["family_id"]))
    earned = round(approved_hours * rate, 2)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dinaro_chore_logs SET status = 'approved', approved_hours = :hours WHERE id = :id"
            ),
            {"hours": approved_hours, "id": log_id},
        )

    _dinaro_add_ledger(int(row["child_id"]), earned, "Chore approved", log_id=log_id)
    _dinaro_check_group_rewards(int(row["family_id"]))
    notify_child(int(row["family_id"]), int(row["child_id"]),
                 "Chore approved!", f"You earned {earned:.2f} dinaro. Nice work!")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/log/<int:log_id>/deny")
def dinaro_parent_deny_log(log_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    conn = get_connection()
    try:
        log_row = conn.execute(
            text("SELECT l.child_id, ch.family_id FROM dinaro_chore_logs l "
                 "JOIN dinaro_children ch ON ch.id = l.child_id WHERE l.id = :id"),
            {"id": log_id},
        ).mappings().first()
    finally:
        conn.close()

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_chore_logs SET status = 'denied', approved_hours = 0 WHERE id = :id"),
            {"id": log_id},
        )

    if log_row:
        notify_child(int(log_row["family_id"]), int(log_row["child_id"]),
                     "Chore not approved", "A parent didn't approve your chore. Check your dashboard.")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/request/<int:request_id>/counter")
def dinaro_parent_counter_request(request_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    counter = safe_float(request.form.get("counter_dinaro"), 0.0)
    note = (request.form.get("parent_note") or "").strip()
    if counter <= 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dinaro_requests
                SET parent_counter_dinaro = :counter, parent_note = :note, status = 'countered'
                WHERE id = :id
                """
            ),
            {"counter": counter, "note": note or None, "id": request_id},
        )

    conn = get_connection()
    try:
        req_row = conn.execute(
            text("SELECT child_id FROM dinaro_requests WHERE id = :id"), {"id": request_id}
        ).mappings().first()
    finally:
        conn.close()
    if req_row:
        notify_child(_dinaro_child_family_id(int(req_row["child_id"])), int(req_row["child_id"]),
                     "Counter offer!", f"A parent countered with {counter:.2f} dinaro. Check it out!")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/request/<int:request_id>/accept")
def dinaro_parent_accept_request(request_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    final_dinaro = safe_float(request.form.get("final_dinaro"), 0.0)
    note = (request.form.get("parent_note") or "").strip()

    conn = get_connection()
    try:
        row = conn.execute(
            text("SELECT child_id, offer_dinaro, parent_counter_dinaro FROM dinaro_requests WHERE id = :id"),
            {"id": request_id},
        ).mappings().first()
    finally:
        conn.close()

    if not row:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    if final_dinaro <= 0:
        final_dinaro = float(row["parent_counter_dinaro"] or row["offer_dinaro"] or 0)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dinaro_requests
                SET status = 'accepted', final_dinaro = :final, parent_note = :note, closed_at = :closed_at
                WHERE id = :id
                """
            ),
            {"final": final_dinaro, "note": note or None, "closed_at": _dinaro_now(), "id": request_id},
        )

    if final_dinaro > 0:
        _dinaro_add_ledger(int(row["child_id"]), -final_dinaro, "Request accepted", request_id=request_id)

    notify_child(_dinaro_child_family_id(int(row["child_id"])), int(row["child_id"]),
                 "Request accepted!", f"Your request was approved for {final_dinaro:.2f} dinaro.")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/child/<int:child_id>/bonus")
def dinaro_parent_child_bonus(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    amount = safe_float(request.form.get("bonus_amount"), 0.0)
    note = (request.form.get("bonus_note") or "Surprise Bonus!").strip()
    if amount == 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    _dinaro_add_ledger(child_id, amount, f"🎁 {note}")
    notify_child(_dinaro_child_family_id(child_id), child_id,
                 "Surprise bonus!", f"You received {amount:.2f} dinaro! {note}")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/goal/<int:goal_id>/edit")
def dinaro_parent_edit_goal(goal_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    title = (request.form.get("goal_title") or "").strip()
    target = safe_float(request.form.get("goal_target"), 0.0)
    if not title or target <= 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_goals SET title = :title, target_dinaro = :target WHERE id = :id"),
            {"title": title, "target": target, "id": goal_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/goal/<int:goal_id>/delete")
def dinaro_parent_delete_goal(goal_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dinaro_goals WHERE id = :id"),
            {"id": goal_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/request/<int:request_id>/decline")
def dinaro_parent_decline_request(request_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    note = (request.form.get("parent_note") or "").strip()

    conn = get_connection()
    try:
        req_row = conn.execute(
            text("SELECT child_id FROM dinaro_requests WHERE id = :id"), {"id": request_id}
        ).mappings().first()
    finally:
        conn.close()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dinaro_requests
                SET status = 'declined', parent_note = :note, closed_at = :closed_at
                WHERE id = :id
                """
            ),
            {"note": note or None, "closed_at": _dinaro_now(), "id": request_id},
        )

    if req_row:
        notify_child(_dinaro_child_family_id(int(req_row["child_id"])), int(req_row["child_id"]),
                     "Request declined", "A parent declined your request. Check your dashboard.")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.route("/child/login", methods=["GET", "POST"])
def dinaro_child_login():
    family_code = session.get("dinaro_family_code")
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "find_family":
            code = (request.form.get("family_code") or "").strip().upper()
            conn = get_connection()
            try:
                family = conn.execute(
                    text("SELECT id, is_classroom FROM dinaro_families WHERE family_code = :code"),
                    {"code": code},
                ).mappings().first()
                if family:
                    session["dinaro_family_code"] = code
                    return redirect(url_for("dinaro.dinaro_child_login"))
                else:
                    return render_template("dinaro_child_login.html", error="Family code not found.")
            finally:
                conn.close()
                
        elif action == "login":
            pin = (request.form.get("child_pin") or "").strip()
            child_id = request.form.get("child_id")
            
            if not family_code:
                return redirect(url_for("dinaro.dinaro_child_login"))
                
            if not child_id or not pin:
                conn = get_connection()
                try:
                    kids = conn.execute(
                        text("""
                            SELECT c.id, c.name FROM dinaro_children c
                            JOIN dinaro_families f ON f.id = c.family_id
                            WHERE f.family_code = :code AND c.approved = 1
                            ORDER BY c.name ASC
                        """),
                        {"code": family_code}
                    ).mappings().all()
                finally:
                    conn.close()
                return render_template("dinaro_child_login.html", error="Choose your name and enter PIN.", kids=kids, family_code=family_code)

            conn = get_connection()
            try:
                row = conn.execute(
                    text("SELECT id, pin_hash, pin_salt FROM dinaro_children WHERE id = :id"),
                    {"id": child_id},
                ).mappings().first()
            finally:
                conn.close()

            if not row or not _verify_pin(pin, row["pin_hash"], row["pin_salt"]):
                conn = get_connection()
                try:
                    kids = conn.execute(
                        text("""
                            SELECT c.id, c.name FROM dinaro_children c
                            JOIN dinaro_families f ON f.id = c.family_id
                            WHERE f.family_code = :code AND c.approved = 1
                            ORDER BY c.name ASC
                        """),
                        {"code": family_code}
                    ).mappings().all()
                finally:
                    conn.close()
                return render_template("dinaro_child_login.html", error="Wrong PIN.", kids=kids, family_code=family_code)

            session["dinaro_child_id"] = int(row["id"])
            session.pop("dinaro_parent_id", None)
            return redirect(url_for("dinaro.dinaro_child_dashboard"))

    if not family_code:
        return render_template("dinaro_child_login.html")

    conn = get_connection()
    try:
        family_row = conn.execute(
            text("SELECT id, is_classroom FROM dinaro_families WHERE family_code = :code"),
            {"code": family_code}
        ).mappings().first()
        kids = conn.execute(
            text("""
                SELECT c.id, c.name FROM dinaro_children c
                JOIN dinaro_families f ON f.id = c.family_id
                WHERE f.family_code = :code AND c.approved = 1
                ORDER BY c.name ASC
            """),
            {"code": family_code}
        ).mappings().all()
    finally:
        conn.close()

    is_classroom = family_row["is_classroom"] if family_row else 0

    if not kids and not is_classroom:
        session.pop("dinaro_family_code", None)
        return render_template("dinaro_child_login.html", error="No children found for this family code.")

    return render_template("dinaro_child_login.html", kids=kids, family_code=family_code, is_classroom=is_classroom)


@dinaro_bp.post("/child/reset-family")
def dinaro_child_reset_family():
    session.pop("dinaro_family_code", None)
    return redirect(url_for("dinaro.dinaro_child_login"))


@dinaro_bp.post("/child/logout")
def dinaro_child_logout():
    session.pop("dinaro_child_id", None)
    return redirect(url_for("dinaro.dinaro_landing"))


@dinaro_bp.post("/child/enroll")
def dinaro_child_enroll():
    """Self-enrollment for classroom mode."""
    family_code = session.get("dinaro_family_code")
    if not family_code:
        return redirect(url_for("dinaro.dinaro_child_login"))

    conn = get_connection()
    try:
        family = conn.execute(
            text("SELECT id, is_classroom FROM dinaro_families WHERE family_code = :code"),
            {"code": family_code},
        ).mappings().first()
    finally:
        conn.close()

    if not family or not family["is_classroom"]:
        return redirect(url_for("dinaro.dinaro_child_login"))

    name = (request.form.get("student_name") or "").strip()
    pin = (request.form.get("student_pin") or "").strip()
    pin_confirm = (request.form.get("student_pin_confirm") or "").strip()

    if not name or not pin or pin != pin_confirm:
        return redirect(url_for("dinaro.dinaro_child_login"))

    pin_hash, pin_salt = _make_pin(pin)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dinaro_children (family_id, name, pin_hash, pin_salt, approved) "
                "VALUES (:fid, :name, :hash, :salt, 0)"
            ),
            {"fid": family["id"], "name": name, "hash": pin_hash, "salt": pin_salt},
        )

    notify_parents(family["id"], "New enrollment request",
                   f"{name} wants to join your class.")

    return render_template("dinaro_child_login.html",
                           family_code=family_code,
                           is_classroom=1,
                           kids=[],
                           success=f"{name}, your request has been sent. Wait for your teacher to approve it.")


@dinaro_bp.post("/parent/enrollment/<int:child_id>/approve")
def dinaro_parent_approve_enrollment(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_children SET approved = 1 WHERE id = :id AND family_id = :fid AND approved = 0"),
            {"id": child_id, "fid": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/enrollment/<int:child_id>/deny")
def dinaro_parent_deny_enrollment(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dinaro_children WHERE id = :id AND family_id = :fid AND approved = 0"),
            {"id": child_id, "fid": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/group-reward/add")
def dinaro_parent_add_group_reward():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("reward_title") or "").strip()
    reward_dinaro = safe_float(request.form.get("reward_dinaro"), 0.0)
    condition_type = request.form.get("condition_type") or "all_complete"
    condition_chore_id = request.form.get("condition_chore_id") or None
    condition_target = request.form.get("condition_target") or None
    condition_period = request.form.get("condition_period") or "daily"

    if not title or reward_dinaro <= 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dinaro_group_rewards
                (family_id, title, reward_dinaro, condition_type, condition_chore_id, condition_target, condition_period)
                VALUES (:fid, :title, :rd, :ct, :cci, :ctarget, :cp)
            """),
            {
                "fid": family_id, "title": title, "rd": reward_dinaro,
                "ct": condition_type,
                "cci": int(condition_chore_id) if condition_chore_id else None,
                "ctarget": int(condition_target) if condition_target else None,
                "cp": condition_period,
            },
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/group-reward/<int:reward_id>/edit")
def dinaro_parent_edit_group_reward(reward_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("reward_title") or "").strip()
    reward_dinaro = safe_float(request.form.get("reward_dinaro"), 0.0)

    if not title or reward_dinaro <= 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_group_rewards SET title = :title, reward_dinaro = :rd WHERE id = :id AND family_id = :fid"),
            {"title": title, "rd": reward_dinaro, "id": reward_id, "fid": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/parent/group-reward/<int:reward_id>/delete")
def dinaro_parent_delete_group_reward(reward_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_group_rewards SET active = 0 WHERE id = :id AND family_id = :fid"),
            {"id": reward_id, "fid": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.get("/child")
def dinaro_child_dashboard():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    _dinaro_process_financials(child_id)

    family_id = _dinaro_child_family_id(child_id)
    rate = _dinaro_rate_for_family(family_id)

    conn = get_connection()
    try:
        child = conn.execute(
            text("SELECT id, family_id, name, balance, view_mode, standing FROM dinaro_children WHERE id = :id"),
            {"id": child_id},
        ).mappings().first()

        family = conn.execute(
            text("SELECT interest_rate, interest_threshold, tax_rate, is_classroom, show_leaderboard, grade_mode FROM dinaro_families WHERE id = :id"),
            {"id": family_id},
        ).mappings().first()

        chores = conn.execute(
            text(
                "SELECT id, title, default_hours, recurrence, chore_type FROM dinaro_chores "
                "WHERE family_id = :id AND active = 1 ORDER BY title ASC"
            ),
            {"id": family_id},
        ).mappings().all()
        spendables = conn.execute(
            text(
                "SELECT id, title, cost_dinaro FROM dinaro_spendables "
                "WHERE family_id = :id AND active = 1 ORDER BY title ASC"
            ),
            {"id": family_id},
        ).mappings().all()
        goals = conn.execute(
            text("SELECT id, title, target_dinaro FROM dinaro_goals WHERE child_id = :id ORDER BY id DESC"),
            {"id": child_id},
        ).mappings().all()
        requests = conn.execute(
            text("SELECT * FROM dinaro_requests WHERE child_id = :id ORDER BY created_at DESC"),
            {"id": child_id},
        ).mappings().all()
        ledger = conn.execute(
            text("SELECT * FROM dinaro_ledger WHERE child_id = :id ORDER BY created_at DESC LIMIT 50"),
            {"id": child_id},
        ).mappings().all()
        recent_logs = conn.execute(
            text("SELECT chore_id, work_date, status FROM dinaro_chore_logs WHERE child_id = :id AND work_date >= :monday"),
            {"id": child_id, "monday": (date.today() - timedelta(days=7)).isoformat()},
        ).mappings().all()
    finally:
        conn.close()

    # Leaderboard (if teacher enabled it)
    show_leaderboard = family.get("show_leaderboard", 0) if family else 0
    leaderboard = []
    if show_leaderboard and family.get("is_classroom"):
        analytics = _dinaro_class_analytics(family_id)
        leaderboard = analytics.get("leaderboard", [])

    # Calculate Quirky Badges
    badges = []
    if (child["balance"] or 0) >= 50:
        badges.append({"emoji": "🏦", "title": "Big Saver", "desc": "Reached 50 Dinaro!"})
    if (child["balance"] or 0) >= 100:
        badges.append({"emoji": "🏛️", "title": "Dinaro Duke", "desc": "A true wealth master!"})
    
    chore_count = sum(1 for e in ledger if "Chore approved" in (e["reason"] or ""))
    if chore_count >= 1:
        badges.append({"emoji": "⚒️", "title": "First Job" if not (family and family.get("is_classroom")) else "First Task", "desc": "Earned your first Dinaro!"})
    if chore_count >= 10:
        badges.append({"emoji": "🏆", "title": "Master Worker", "desc": "10 jobs finished!" if not (family and family.get("is_classroom")) else "10 tasks finished!"})
    
    bonus_count = sum(1 for e in ledger if "🎁" in (e["reason"] or ""))
    if bonus_count >= 1:
        badges.append({"emoji": "🍀", "title": "Lucky One", "desc": "Got a surprise bonus!"})

    # Calculate To-Do List (Recurring Chores)
    todo_list = []
    completed_today = []
    today = date.today().isoformat()
    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()

    for chore in chores:
        if chore["recurrence"] == "daily":
            done_logs = any(l["chore_id"] == chore["id"] and l["work_date"] == today and l["status"] != 'denied' for l in recent_logs)

            if done_logs:
                completed_today.append(chore)
            else:
                todo_list.append(chore)
        elif chore["recurrence"] == "weekly":
            done_logs = any(l["chore_id"] == chore["id"] and l["work_date"] >= monday and l["status"] != 'denied' for l in recent_logs)

            if done_logs:
                was_today_logs = any(l["chore_id"] == chore["id"] and l["work_date"] == today and l["status"] != 'denied' for l in recent_logs)
                if was_today_logs:
                    completed_today.append(chore)
            else:
                todo_list.append(chore)

    treasury = _dinaro_active_fund(family_id)
    my_bill = None
    classmates = []
    treasury_options = []
    my_vote = None
    can_vote = False
    if treasury:
        conn = get_connection()
        try:
            my_bill = conn.execute(
                text("SELECT amount_owed, amount_paid FROM dinaro_fund_bills WHERE fund_id=:f AND child_id=:c"),
                {"f": treasury["id"], "c": child_id},
            ).mappings().first()
            classmates = conn.execute(
                text("SELECT id, name FROM dinaro_children WHERE family_id=:fid AND approved=1 AND id != :c ORDER BY name ASC"),
                {"fid": family_id, "c": child_id},
            ).mappings().all()
            treasury_options = conn.execute(
                text(
                    "SELECT o.id, o.label, "
                    "(SELECT COUNT(*) FROM dinaro_fund_votes v WHERE v.option_id = o.id) AS votes "
                    "FROM dinaro_fund_options o WHERE o.fund_id = :f ORDER BY o.id"
                ),
                {"f": treasury["id"]},
            ).mappings().all()
            if treasury["status"] == "voting":
                if not treasury["penalty_no_vote"]:
                    can_vote = True
                elif my_bill:
                    owed, paid = float(my_bill["amount_owed"]), float(my_bill["amount_paid"])
                    can_vote = owed <= 0 or paid >= owed
                mv = conn.execute(
                    text("SELECT option_id FROM dinaro_fund_votes WHERE fund_id=:f AND child_id=:c"),
                    {"f": treasury["id"], "c": child_id},
                ).mappings().first()
                my_vote = mv["option_id"] if mv else None
        finally:
            conn.close()

    return render_template(
        "dinaro_child_dashboard.html",
        child=child,
        chores=chores,
        todo_list=todo_list,
        completed_today=completed_today,
        spendables=spendables,
        goals=goals,
        requests=requests,
        ledger=ledger,
        rate_per_hour=rate,
        family=family,
        badges=badges,
        interest_rate=family.get("interest_rate", 0),
        interest_threshold=family.get("interest_threshold", 100),
        tax_rate=family.get("tax_rate", 0),
        leaderboard=leaderboard,
        show_leaderboard=show_leaderboard,
        treasury=treasury,
        my_bill=my_bill,
        classmates=classmates,
        treasury_options=treasury_options,
        my_vote=my_vote,
        can_vote=can_vote,
    )


# ----------------------------
# The Treasury — student actions
# ----------------------------
def _dinaro_fund_match(fund, amount: float) -> float:
    """Teacher match added on top of a contribution (match_num : match_den)."""
    den = fund["match_den"] or 1
    return round(amount * (fund["match_num"] or 0) / den, 2)


@dinaro_bp.post("/child/treasury/pay")
def dinaro_child_treasury_pay():
    """Pay toward your fair-share bill (capped at what you owe and what you have)."""
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))
    family_id = _dinaro_child_family_id(child_id)
    fund = _dinaro_active_fund(family_id)
    amount = safe_float(request.form.get("amount"), 0.0)
    if fund and amount > 0:
        with engine.begin() as conn:
            bal = float(conn.execute(text("SELECT balance FROM dinaro_children WHERE id=:c"), {"c": child_id}).scalar() or 0)
            bill = conn.execute(
                text("SELECT id, amount_owed, amount_paid FROM dinaro_fund_bills WHERE fund_id=:f AND child_id=:c"),
                {"f": fund["id"], "c": child_id},
            ).mappings().first()
            if bill:
                remaining = max(0.0, float(bill["amount_owed"]) - float(bill["amount_paid"]))
                pay = round(min(amount, bal, remaining), 2)
                if pay > 0:
                    conn.execute(text("UPDATE dinaro_children SET balance = balance - :p WHERE id=:c"), {"p": pay, "c": child_id})
                    conn.execute(text("UPDATE dinaro_fund_bills SET amount_paid = amount_paid + :p WHERE id=:b"), {"p": pay, "b": bill["id"]})
                    conn.execute(text("UPDATE dinaro_class_funds SET raised = raised + :amt WHERE id=:f"), {"amt": pay + _dinaro_fund_match(fund, pay), "f": fund["id"]})
                    conn.execute(text("INSERT INTO dinaro_ledger (child_id, delta, reason, created_at) VALUES (:c, :d, 'treasury_tax', :now)"), {"c": child_id, "d": -pay, "now": _dinaro_now()})
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/treasury/donate")
def dinaro_child_treasury_donate():
    """Donate extra to the Treasury (beyond your bill)."""
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))
    family_id = _dinaro_child_family_id(child_id)
    fund = _dinaro_active_fund(family_id)
    amount = safe_float(request.form.get("amount"), 0.0)
    if fund and amount > 0:
        with engine.begin() as conn:
            bal = float(conn.execute(text("SELECT balance FROM dinaro_children WHERE id=:c"), {"c": child_id}).scalar() or 0)
            give = round(min(amount, bal), 2)
            if give > 0:
                conn.execute(text("UPDATE dinaro_children SET balance = balance - :g WHERE id=:c"), {"g": give, "c": child_id})
                conn.execute(text("UPDATE dinaro_class_funds SET raised = raised + :amt WHERE id=:f"), {"amt": give + _dinaro_fund_match(fund, give), "f": fund["id"]})
                conn.execute(text("INSERT INTO dinaro_ledger (child_id, delta, reason, created_at) VALUES (:c, :d, 'treasury_donation', :now)"), {"c": child_id, "d": -give, "now": _dinaro_now()})
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/treasury/grade")
def dinaro_child_treasury_grade():
    """Spend dinaro to raise your own grade/standing (1:1)."""
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))
    amount = safe_float(request.form.get("amount"), 0.0)
    if amount > 0:
        with engine.begin() as conn:
            bal = float(conn.execute(text("SELECT balance FROM dinaro_children WHERE id=:c"), {"c": child_id}).scalar() or 0)
            spend = round(min(amount, bal), 2)
            if spend > 0:
                conn.execute(text("UPDATE dinaro_children SET balance = balance - :s, standing = standing + :s WHERE id=:c"), {"s": spend, "c": child_id})
                conn.execute(text("INSERT INTO dinaro_ledger (child_id, delta, reason, created_at) VALUES (:c, :d, 'grade_self', :now)"), {"c": child_id, "d": -spend, "now": _dinaro_now()})
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/treasury/gift")
def dinaro_child_treasury_gift():
    """Spend your dinaro to raise a classmate's grade/standing (1:1)."""
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))
    family_id = _dinaro_child_family_id(child_id)
    target_id = int(safe_float(request.form.get("target_id"), 0))
    amount = safe_float(request.form.get("amount"), 0.0)
    if target_id and target_id != child_id and amount > 0:
        with engine.begin() as conn:
            target = conn.execute(
                text("SELECT id FROM dinaro_children WHERE id=:t AND family_id=:fid AND approved=1"),
                {"t": target_id, "fid": family_id},
            ).mappings().first()
            bal = float(conn.execute(text("SELECT balance FROM dinaro_children WHERE id=:c"), {"c": child_id}).scalar() or 0)
            spend = round(min(amount, bal), 2)
            if target and spend > 0:
                conn.execute(text("UPDATE dinaro_children SET balance = balance - :s WHERE id=:c"), {"s": spend, "c": child_id})
                conn.execute(text("UPDATE dinaro_children SET standing = standing + :s WHERE id=:t"), {"s": spend, "t": target_id})
                conn.execute(text("INSERT INTO dinaro_ledger (child_id, delta, reason, created_at) VALUES (:c, :d, 'grade_gift', :now)"), {"c": child_id, "d": -spend, "now": _dinaro_now()})
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/treasury/vote")
def dinaro_child_treasury_vote():
    """Cast (or change) your vote on what the funded Treasury does."""
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))
    family_id = _dinaro_child_family_id(child_id)
    fund = _dinaro_active_fund(family_id)
    option_id = int(safe_float(request.form.get("option_id"), 0))
    if not (fund and fund["status"] == "voting" and option_id):
        return redirect(url_for("dinaro.dinaro_child_dashboard"))
    with engine.begin() as conn:
        opt = conn.execute(
            text("SELECT id FROM dinaro_fund_options WHERE id=:o AND fund_id=:f"),
            {"o": option_id, "f": fund["id"]},
        ).mappings().first()
        # Gate: if the teacher set "no vote unless paid", require the bill be settled.
        eligible = True
        if fund["penalty_no_vote"]:
            bill = conn.execute(
                text("SELECT amount_owed, amount_paid FROM dinaro_fund_bills WHERE fund_id=:f AND child_id=:c"),
                {"f": fund["id"], "c": child_id},
            ).mappings().first()
            if not bill:
                eligible = False
            else:
                owed, paid = float(bill["amount_owed"]), float(bill["amount_paid"])
                eligible = owed <= 0 or paid >= owed
        if opt and eligible:
            conn.execute(text("DELETE FROM dinaro_fund_votes WHERE fund_id=:f AND child_id=:c"), {"f": fund["id"], "c": child_id})
            conn.execute(
                text("INSERT INTO dinaro_fund_votes (fund_id, child_id, option_id) VALUES (:f, :c, :o)"),
                {"f": fund["id"], "c": child_id, "o": option_id},
            )
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.get("/child/history")
def dinaro_child_history():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    conn = get_connection()
    try:
        child = conn.execute(
            text("SELECT id, family_id, name, balance, view_mode FROM dinaro_children WHERE id = :id"),
            {"id": child_id},
        ).mappings().first()

        ledger = conn.execute(
            text("SELECT * FROM dinaro_ledger WHERE child_id = :id ORDER BY created_at DESC"),
            {"id": child_id},
        ).mappings().all()
    finally:
        conn.close()

    return render_template(
        "dinaro_child_history.html",
        child=child,
        ledger=ledger,
    )


@dinaro_bp.post("/child/log-chore")
def dinaro_child_log_chore():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    chore_id = request.form.get("chore_id")
    overtime_hours = safe_float(request.form.get("overtime_hours"), 0.0)

    conn = get_connection()
    try:
        chore = conn.execute(
            text("SELECT id, title, default_hours FROM dinaro_chores WHERE id = :id"),
            {"id": chore_id},
        ).mappings().first()
    finally:
        conn.close()

    if not chore:
        return redirect(url_for("dinaro.dinaro_child_dashboard"))

    requested_hours = float(chore["default_hours"]) + max(0.0, overtime_hours)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dinaro_chore_logs (child_id, chore_id, work_date, overtime_hours, requested_hours, created_at)
                VALUES (:child_id, :chore_id, :work_date, :overtime_hours, :requested_hours, :created_at)
                """
            ),
            {
                "child_id": child_id,
                "chore_id": chore_id,
                "work_date": date.today().isoformat(),
                "overtime_hours": max(0.0, overtime_hours),
                "requested_hours": requested_hours,
                "created_at": _dinaro_now(),
            },
        )

    family_id = _dinaro_child_family_id(child_id)
    conn = get_connection()
    try:
        child_row = conn.execute(
            text("SELECT name FROM dinaro_children WHERE id = :id"), {"id": child_id}
        ).mappings().first()
    finally:
        conn.close()
    child_name = child_row["name"] if child_row else "Your child"
    chore_title = chore["title"] or "a chore"
    notify_parents(family_id, f"{child_name} finished a chore",
                   f"{child_name} completed '{chore_title}' and needs approval.")
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/goal/add")
def dinaro_child_add_goal():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    title = (request.form.get("goal_title") or "").strip()
    target = safe_float(request.form.get("goal_target"), 0.0)
    if not title or target <= 0:
        return redirect(url_for("dinaro.dinaro_child_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO dinaro_goals (child_id, title, target_dinaro) VALUES (:id, :title, :target)"),
            {"id": child_id, "title": title, "target": target},
        )
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/goal/<int:goal_id>/delete")
def dinaro_child_delete_goal(goal_id: int):
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dinaro_goals WHERE id = :id AND child_id = :child_id"),
            {"id": goal_id, "child_id": child_id},
        )
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/request/add")
def dinaro_child_add_request():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    spendable_id = request.form.get("spendable_id")
    custom_name = (request.form.get("item_name") or "").strip()
    
    if spendable_id:
        conn = get_connection()
        try:
            item = conn.execute(
                text("SELECT title, cost_dinaro FROM dinaro_spendables WHERE id = :id"),
                {"id": spendable_id}
            ).mappings().first()
        finally:
            conn.close()
        
        if not item:
            return redirect(url_for("dinaro.dinaro_child_dashboard"))
        
        item_name = item["title"]
        item_cost = float(item["cost_dinaro"])
        offer = safe_float(request.form.get("offer_dinaro"), item_cost)
    else:
        item_name = custom_name
        item_cost = safe_float(request.form.get("item_cost_dinaro"), 0.0)
        offer = safe_float(request.form.get("offer_dinaro"), 0.0)

    note = (request.form.get("child_note") or "").strip()

    if not item_name or item_cost <= 0 or offer <= 0:
        return redirect(url_for("dinaro.dinaro_child_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dinaro_requests
                (child_id, item_name, item_cost_dinaro, offer_dinaro, status, child_note, created_at)
                VALUES (:child_id, :item_name, :item_cost, :offer, 'open', :note, :created_at)
                """
            ),
            {
                "child_id": child_id,
                "item_name": item_name,
                "item_cost": item_cost,
                "offer": offer,
                "note": note or None,
                "created_at": _dinaro_now(),
            },
        )

    family_id = _dinaro_child_family_id(child_id)
    conn = get_connection()
    try:
        child_row = conn.execute(
            text("SELECT name FROM dinaro_children WHERE id = :id"), {"id": child_id}
        ).mappings().first()
    finally:
        conn.close()
    child_name = child_row["name"] if child_row else "Your child"
    notify_parents(family_id, f"{child_name} wants something!",
                   f"{child_name} requested '{item_name}' for {offer:.2f} dinaro.")
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/child/request/<int:request_id>/update")
def dinaro_child_update_request(request_id: int):
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    offer = safe_float(request.form.get("offer_dinaro"), 0.0)
    note = (request.form.get("child_note") or "").strip()
    if offer <= 0:
        return redirect(url_for("dinaro.dinaro_child_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dinaro_requests
                SET offer_dinaro = :offer, child_note = :note, status = 'open'
                WHERE id = :id AND child_id = :child_id AND status IN ('open', 'countered')
                """
            ),
            {"offer": offer, "note": note or None, "id": request_id, "child_id": child_id},
        )
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.get("/parent/export")
def dinaro_parent_export():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    conn = get_connection()
    try:
        ledger = conn.execute(
            text(
                """
                SELECT l.created_at, ch.name AS child_name, l.delta, l.reason, l.log_id, l.request_id
                FROM dinaro_ledger l
                JOIN dinaro_children ch ON ch.id = l.child_id
                WHERE ch.family_id = :id
                ORDER BY l.created_at DESC
                """
            ),
            {"id": family_id},
        ).mappings().all()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Child", "Amount", "Reason", "Type"])

    for entry in ledger:
        dt_str = entry["created_at"][:19].replace("T", " ")
        entry_type = "Other"
        if entry["log_id"]:
            entry_type = "Chore"
        elif entry["request_id"]:
            entry_type = "Reward/Request"
        elif "Savings Bonus" in (entry["reason"] or ""):
            entry_type = "Interest"
        elif "Parent Tax" in (entry["reason"] or "") or "Subscription" in (entry["reason"] or ""):
            entry_type = "Tax"

        writer.writerow([dt_str, entry["child_name"], f"{entry['delta']:.2f}", entry["reason"], entry_type])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=dinaro_history.csv"},
    )


@dinaro_bp.get("/parent/export/child/<int:child_id>")
def dinaro_parent_export_child(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    conn = get_connection()
    try:
        child = conn.execute(
            text("SELECT name FROM dinaro_children WHERE id = :id AND family_id = :fid"),
            {"id": child_id, "fid": family_id},
        ).mappings().first()

        if not child:
            return redirect(url_for("dinaro.dinaro_parent_dashboard"))

        ledger = conn.execute(
            text(
                """
                SELECT created_at, delta, reason, log_id, request_id
                FROM dinaro_ledger
                WHERE child_id = :id
                ORDER BY created_at DESC
                """
            ),
            {"id": child_id},
        ).mappings().all()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Amount", "Reason", "Type"])

    for entry in ledger:
        dt_str = entry["created_at"][:19].replace("T", " ")
        entry_type = "Other"
        if entry["log_id"]:
            entry_type = "Chore"
        elif entry["request_id"]:
            entry_type = "Reward/Request"
        elif "Savings Bonus" in (entry["reason"] or ""):
            entry_type = "Interest"
        elif "Parent Tax" in (entry["reason"] or "") or "Subscription" in (entry["reason"] or ""):
            entry_type = "Tax"

        writer.writerow([dt_str, f"{entry['delta']:.2f}", entry["reason"], entry_type])

    output.seek(0)
    filename = f"dinaro_history_{child['name'].lower().replace(' ', '_')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"},
    )


# ----------------------------
# Push Notification API
# ----------------------------

@dinaro_bp.get("/push/vapid-public-key")
def dinaro_push_vapid_key():
    import os
    return jsonify({"publicKey": os.environ.get("VAPID_PUBLIC_KEY", "")})


@dinaro_bp.post("/push/subscribe")
def dinaro_push_subscribe():
    from dinaro.push import save_subscription

    sub_json = request.get_json(silent=True)
    if not sub_json or "endpoint" not in sub_json or "keys" not in sub_json:
        return jsonify({"error": "Invalid subscription"}), 400

    parent_id = session.get("dinaro_parent_id")
    child_id = session.get("dinaro_child_id")

    if parent_id:
        family_id = _dinaro_parent_family_id(int(parent_id))
        save_subscription(family_id, "parent", int(parent_id), sub_json)
        return jsonify({"ok": True})
    elif child_id:
        family_id = _dinaro_child_family_id(int(child_id))
        save_subscription(family_id, "child", int(child_id), sub_json)
        return jsonify({"ok": True})
    else:
        return jsonify({"error": "Not logged in"}), 401


@dinaro_bp.post("/push/unsubscribe")
def dinaro_push_unsubscribe():
    from dinaro.push import remove_subscription_by_endpoint

    data = request.get_json(silent=True)
    if not data or "endpoint" not in data:
        return jsonify({"error": "Missing endpoint"}), 400

    remove_subscription_by_endpoint(data["endpoint"])
    return jsonify({"ok": True})
