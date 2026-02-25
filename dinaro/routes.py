from __future__ import annotations

import hashlib
import secrets
import csv
import io
from datetime import date, datetime, timedelta
from typing import Optional

from flask import render_template, request, session, redirect, url_for, Response
from sqlalchemy import text

from database import engine, get_db_connection as get_connection
from . import dinaro_bp


# ----------------------------
# Dinaro Helpers
# ----------------------------

def _pin_hash(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()


def _make_pin(pin: str) -> tuple[str, str]:
    salt = secrets.token_hex(8)
    return _pin_hash(pin, salt), salt


def _verify_pin(pin: str, pin_hash: str, salt: str) -> bool:
    return _pin_hash(pin, salt) == pin_hash


def _dinaro_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


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


# ----------------------------
# Dinaro Routes
# ----------------------------

@dinaro_bp.get("/dinaro")
def dinaro_landing():
    if session.get("dinaro_parent_id"):
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))
    if session.get("dinaro_child_id"):
        return redirect(url_for("dinaro.dinaro_child_dashboard"))
    return render_template("dinaro_landing.html")


@dinaro_bp.route("/dinaro/setup", methods=["GET", "POST"])
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

        if not parent_name or not pin or pin != pin_confirm:
            return render_template("dinaro_setup.html", error="Please check name and matching PIN.")

        pin_hash, pin_salt = _make_pin(pin)

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO dinaro_families (name, rate_per_hour, family_code) "
                    "VALUES (:name, :rate, :code) RETURNING id"
                ),
                {"name": family_name or None, "rate": 4, "code": _dinaro_make_family_code()},
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


@dinaro_bp.route("/dinaro/parent/login", methods=["GET", "POST"])
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


@dinaro_bp.post("/dinaro/parent/logout")
def dinaro_parent_logout():
    session.pop("dinaro_parent_id", None)
    return redirect(url_for("dinaro.dinaro_landing"))


@dinaro_bp.route("/dinaro/child/login", methods=["GET", "POST"])
def dinaro_child_login():
    # Two-step login for safety:
    # 1. Family Code (persisted in session)
    # 2. Child Picker + PIN
    
    family_code = session.get("dinaro_family_code")
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "find_family":
            code = (request.form.get("family_code") or "").strip().upper()
            conn = get_connection()
            try:
                family = conn.execute(
                    text("SELECT id FROM dinaro_families WHERE family_code = :code"),
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
                            WHERE f.family_code = :code
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
                            WHERE f.family_code = :code
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

    # GET
    if not family_code:
        return render_template("dinaro_child_login.html")
    
    conn = get_connection()
    try:
        kids = conn.execute(
            text("""
                SELECT c.id, c.name FROM dinaro_children c
                JOIN dinaro_families f ON f.id = c.family_id
                WHERE f.family_code = :code
                ORDER BY c.name ASC
            """),
            {"code": family_code}
        ).mappings().all()
    finally:
        conn.close()
        
    if not kids:
        # If family exists but has no kids yet
        session.pop("dinaro_family_code", None)
        return render_template("dinaro_child_login.html", error="No children found for this family code.")
        
    return render_template("dinaro_child_login.html", kids=kids, family_code=family_code)


@dinaro_bp.post("/dinaro/child/reset-family")
def dinaro_child_reset_family():
    session.pop("dinaro_family_code", None)
    return redirect(url_for("dinaro.dinaro_child_login"))


@dinaro_bp.post("/dinaro/child/logout")
def dinaro_child_logout():
    session.pop("dinaro_child_id", None)
    return redirect(url_for("dinaro.dinaro_landing"))


@dinaro_bp.get("/dinaro/parent/export")
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
                SELECT l.created_at, ch.name AS child_name, l.delta, l.reason
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
    writer.writerow(["Date", "Child", "Amount", "Reason"])

    for entry in ledger:
        # Format date for CSV
        dt_str = entry["created_at"][:19].replace("T", " ")
        writer.writerow([dt_str, entry["child_name"], f"{entry['delta']:.2f}", entry["reason"]])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=dinaro_history.csv"},
    )


@dinaro_bp.get("/dinaro/parent")
def dinaro_parent_dashboard():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    conn = get_connection()
    try:
        family = conn.execute(
            text("SELECT id, name, rate_per_hour, family_code FROM dinaro_families WHERE id = :id"),
            {"id": family_id},
        ).mappings().first()
        kids = conn.execute(
            text("SELECT id, name, balance, view_mode FROM dinaro_children WHERE family_id = :id ORDER BY name ASC"),
            {"id": family_id},
        ).mappings().all()
        chores = conn.execute(
            text(
                "SELECT id, title, default_hours FROM dinaro_chores "
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
    finally:
        conn.close()

    return render_template(
        "dinaro_parent_dashboard.html",
        family=family,
        kids=kids,
        chores=chores,
        spendables=spendables,
        pending_logs=pending_logs,
        requests=requests,
        goals=goals,
        ledger=ledger,
        rate_per_hour=family["rate_per_hour"] if family else 4,
    )


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@dinaro_bp.post("/dinaro/parent/settings")
def dinaro_parent_settings():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    name = (request.form.get("family_name") or "").strip() or None
    rate = safe_float(request.form.get("rate_per_hour"), 4.0)
    if rate <= 0:
        rate = 4.0

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_families SET name = :name, rate_per_hour = :rate WHERE id = :id"),
            {"name": name, "rate": rate, "id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/child/add")
def dinaro_parent_add_child():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
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


@dinaro_bp.post("/dinaro/parent/child/<int:child_id>/edit")
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


@dinaro_bp.post("/dinaro/parent/child/<int:child_id>/mode")
def dinaro_parent_update_child_mode(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    mode = (request.form.get("view_mode") or "visual").strip()

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dinaro_children SET view_mode = :mode "
                "WHERE id = :id AND family_id = :family_id"
            ),
            {"mode": mode, "id": child_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/parent/add")
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


@dinaro_bp.post("/dinaro/parent/chore/add")
def dinaro_parent_add_chore():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("chore_title") or "").strip()
    default_hours = safe_float(request.form.get("default_hours"), 0.0)
    recurrence = (request.form.get("recurrence") or "none").strip()
    if not title or default_hours <= 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dinaro_chores (family_id, title, default_hours, recurrence) "
                "VALUES (:family_id, :title, :default_hours, :recurrence)"
            ),
            {"family_id": family_id, "title": title, "default_hours": default_hours, "recurrence": recurrence},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/chore/<int:chore_id>/edit")
def dinaro_parent_edit_chore(chore_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("chore_title") or "").strip()
    default_hours = safe_float(request.form.get("default_hours"), 0.0)
    recurrence = (request.form.get("recurrence") or "none").strip()

    if not title or default_hours <= 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dinaro_chores SET title = :title, default_hours = :default_hours, recurrence = :recurrence "
                "WHERE id = :id AND family_id = :family_id"
            ),
            {"title": title, "default_hours": default_hours, "recurrence": recurrence, "id": chore_id, "family_id": family_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/chore/<int:chore_id>/delete")
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


@dinaro_bp.post("/dinaro/parent/spendable/add")
def dinaro_parent_add_spendable():
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("spendable_title") or "").strip()
    cost = safe_float(request.form.get("cost_dinaro"), 0.0)
    if not title or cost <= 0:
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


@dinaro_bp.post("/dinaro/parent/spendable/<int:spendable_id>/edit")
def dinaro_parent_edit_spendable(spendable_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    family_id = _dinaro_parent_family_id(parent_id)
    title = (request.form.get("spendable_title") or "").strip()
    cost = safe_float(request.form.get("cost_dinaro"), 0.0)

    if not title or cost <= 0:
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


@dinaro_bp.post("/dinaro/parent/spendable/<int:spendable_id>/delete")
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


@dinaro_bp.post("/dinaro/parent/log/<int:log_id>/approve")
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
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/log/<int:log_id>/deny")
def dinaro_parent_deny_log(log_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dinaro_chore_logs SET status = 'denied', approved_hours = 0 WHERE id = :id"),
            {"id": log_id},
        )
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/request/<int:request_id>/counter")
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
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/request/<int:request_id>/accept")
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

    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/child/<int:child_id>/bonus")
def dinaro_parent_child_bonus(child_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    amount = safe_float(request.form.get("bonus_amount"), 0.0)
    note = (request.form.get("bonus_note") or "Surprise Bonus!").strip()
    if amount == 0:
        return redirect(url_for("dinaro.dinaro_parent_dashboard"))

    _dinaro_add_ledger(child_id, amount, f"🎁 {note}")
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.post("/dinaro/parent/request/<int:request_id>/decline")
def dinaro_parent_decline_request(request_id: int):
    parent_id = _dinaro_require_parent()
    if not parent_id:
        return redirect(url_for("dinaro.dinaro_parent_login"))

    note = (request.form.get("parent_note") or "").strip()
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
    return redirect(url_for("dinaro.dinaro_parent_dashboard"))


@dinaro_bp.get("/dinaro/child")
def dinaro_child_dashboard():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    family_id = _dinaro_child_family_id(child_id)
    rate = _dinaro_rate_for_family(family_id)

    conn = get_connection()
    try:
        child = conn.execute(
            text("SELECT id, name, balance, view_mode FROM dinaro_children WHERE id = :id"),
            {"id": child_id},
        ).mappings().first()
        chores = conn.execute(
            text(
                "SELECT id, title, default_hours, recurrence FROM dinaro_chores "
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
    finally:
        conn.close()

    # Calculate Quirky Badges
    badges = []
    if (child["balance"] or 0) >= 50:
        badges.append({"emoji": "🏦", "title": "Big Saver", "desc": "Reached 50 Dinaro!"})
    if (child["balance"] or 0) >= 100:
        badges.append({"emoji": "🏛️", "title": "Dinaro Duke", "desc": "A true wealth master!"})
    
    chore_count = sum(1 for e in ledger if "Chore approved" in (e["reason"] or ""))
    if chore_count >= 1:
        badges.append({"emoji": "⚒️", "title": "First Job", "desc": "Earned your first Dinaro!"})
    if chore_count >= 10:
        badges.append({"emoji": "🏆", "title": "Master Worker", "desc": "10 jobs finished!"})
    
    bonus_count = sum(1 for e in ledger if "🎁" in (e["reason"] or ""))
    if bonus_count >= 1:
        badges.append({"emoji": "🍀", "title": "Lucky One", "desc": "Got a surprise bonus!"})

    # Calculate To-Do List (Recurring Chores)
    todo_list = []
    today = date.today().isoformat()
    # For weekly, we check logs since last Monday
    monday = (date.today() - timedelta(days=date.today().weekday())).isoformat()

    for chore in chores:
        if chore["recurrence"] == "daily":
            # Check if logged today
            conn = get_connection()
            try:
                done = conn.execute(
                    text("SELECT id FROM dinaro_chore_logs WHERE child_id = :cid AND chore_id = :chid AND work_date = :d"),
                    {"cid": child_id, "chid": chore["id"], "d": today}
                ).mappings().first()
                if not done:
                    todo_list.append(chore)
            finally:
                conn.close()
        elif chore["recurrence"] == "weekly":
            # Check if logged since Monday
            conn = get_connection()
            try:
                done = conn.execute(
                    text("SELECT id FROM dinaro_chore_logs WHERE child_id = :cid AND chore_id = :chid AND work_date >= :m"),
                    {"cid": child_id, "chid": chore["id"], "m": monday}
                ).mappings().first()
                if not done:
                    todo_list.append(chore)
            finally:
                conn.close()

    return render_template(
        "dinaro_child_dashboard.html",
        child=child,
        chores=chores,
        spendables=spendables,
        goals=goals,
        requests=requests,
        ledger=ledger,
        rate_per_hour=rate,
        badges=badges,
        todo_list=todo_list
    )


@dinaro_bp.post("/dinaro/child/chore/log")
def dinaro_child_log_chore():
    child_id = _dinaro_require_child()
    if not child_id:
        return redirect(url_for("dinaro.dinaro_child_login"))

    chore_id = request.form.get("chore_id")
    overtime_hours = safe_float(request.form.get("overtime_hours"), 0.0)

    conn = get_connection()
    try:
        chore = conn.execute(
            text("SELECT id, default_hours FROM dinaro_chores WHERE id = :id"),
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
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/dinaro/child/goal/add")
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


@dinaro_bp.post("/dinaro/child/goal/<int:goal_id>/delete")
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


@dinaro_bp.post("/dinaro/child/request/add")
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
        # If it's from dropdown, child might still make an offer
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
    return redirect(url_for("dinaro.dinaro_child_dashboard"))


@dinaro_bp.post("/dinaro/child/request/<int:request_id>/update")
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


@dinaro_bp.get("/health/dinaro")
def dinaro_health_check():
    """Simple health endpoint to verify blueprint is registered."""
    return {"ok": True, "component": "dinaro"}
