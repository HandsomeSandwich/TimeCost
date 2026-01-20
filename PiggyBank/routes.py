from flask import render_template, request, redirect, url_for, session
from . import piggybank_bp


@piggybank_bp.route("/", methods=["GET", "POST"])
def kiosk_home():
    if request.method == "POST":
        card_uid = (request.form.get("card_uid") or "").strip()
        session["last_card_uid"] = card_uid  # temporary: proves the tap came through
        return redirect(url_for("piggybank.parent_home"))  # temporary redirect

    return render_template("piggybank/tap.html")


@piggybank_bp.get("/parent")
def parent_home():
    return render_template("piggybank/parent_login.html")
