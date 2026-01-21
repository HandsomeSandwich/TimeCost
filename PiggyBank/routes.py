from __future__ import annotations

from flask import render_template, request, redirect, url_for, session, abort
from . import piggybank_bp


def require_parent():
    """Tiny gatekeeper for parent pages."""
    if not session.get("piggy_parent"):
        return redirect(url_for("piggybank.parent_home"))
    return None


@piggybank_bp.route("/", methods=["GET", "POST"])
def kiosk_home():
    """
    Kids kiosk: user taps card, we capture UID in session, then show result page.
    """
    if request.method == "POST":
        card_uid = (request.form.get("card_uid") or "").strip()

        if not card_uid:
            # You can also show a nicer message in tap.html if you want.
            return render_template("piggybank/tap.html", error="No card detected. Try again.")

        session["last_card_uid"] = card_uid
        return redirect(url_for("piggybank.result"))

    return render_template("piggybank/tap.html")


@piggybank_bp.get("/result")
def result():
    """
    Shows the last tap result (temporary: just displays UID).
    Later: lookup balance/limits by card_uid.
    """
    card_uid = session.get("last_card_uid")
    if not card_uid:
        # No tap yet, send them back to the orb 🐷
        return redirect(url_for("piggybank.kiosk_home"))

    # Optional: "consume" the tap so refresh doesn't re-use it forever.
    # If you prefer it to persist, remove this line.
    # session.pop("last_card_uid", None)

    return render_template("piggybank/result.html", card_uid=card_uid)


@piggybank_bp.route("/parent", methods=["GET", "POST"])
def parent_home():
    """
    Parent login page.
    Minimal version: sets piggy_parent=True if password matches an env var.
    """
    if request.method == "POST":
        password = (request.form.get("password") or "").strip()
        expected = (piggybank_bp.root_path and None)  # no-op; keeps linters calm

        # Set this in your env: PIGGY_PARENT_PASSWORD="whatever"
        expected = (session.get("_dummy") and None)  # no-op
        expected = (  # actual read
            __import__("os").environ.get("PIGGY_PARENT_PASSWORD", "")
        )

        if expected and password == expected:
            session["piggy_parent"] = True
            return redirect(url_for("piggybank.parent_dashboard"))

        return render_template("piggybank/parent_login.html", error="Wrong password.")

    return render_template("piggybank/parent_login.html")


@piggybank_bp.get("/parent/dashboard")
def parent_dashboard():
    gate = require_parent()
    if gate:
        return gate

    # Later: pull history/balances from DB. For now, show last tap.
    return render_template(
        "piggybank/parent_dashboard.html",
        last_card_uid=session.get("last_card_uid", ""),
    )


@piggybank_bp.post("/parent/logout")
def parent_logout():
    session.pop("piggy_parent", None)
    return redirect(url_for("piggybank.parent_home"))
