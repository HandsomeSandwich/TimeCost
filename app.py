from __future__ import annotations

import os
import secrets

from flask import Flask, render_template, request, session, redirect

from database import init_db
from core.profile import DEFAULT_CURRENCY, get_effective_hourly_rate

# ----------------------------
# App setup
# ----------------------------
app = Flask(__name__)
# In production, set FLASK_SECRET_KEY in env so sessions persist across restarts.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

# ----------------------------
# Blueprints
# ----------------------------
# Core TimeCost pages (calculator, personal, expenses, budget, …) - no prefix,
# so every URL is identical to before the blueprint split.
from core.routes import core_bp
app.register_blueprint(core_bp)

# Dinaro - family / classroom economy
from dinaro import dinaro_bp
app.register_blueprint(dinaro_bp, url_prefix="/dinaro")

# Couples - making invisible work visible
from couples import couples_bp
app.register_blueprint(couples_bp, url_prefix="/couples")

# Initialize DB on startup
try:
    init_db()
    from dinaro.db import init_dinaro_db
    init_dinaro_db()
    from dinaro.routes import _dinaro_ensure_family_codes
    _dinaro_ensure_family_codes()
except Exception as e:
    print("Database init error:", e)


# ----------------------------
# Error handlers
# ----------------------------
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


# ----------------------------
# Global request hooks
# ----------------------------
@app.before_request
def redirect_www():
    """Redirect www.thetimecost.com → thetimecost.com"""
    if request.host.startswith("www."):
        return redirect(
            request.url.replace("www.", "", 1),
            code=301,
        )


@app.before_request
def ensure_view():
    if "view" not in session:
        session["view"] = "personality"


@app.before_request
def ensure_identity():
    if "user_key" not in session:
        session["user_key"] = secrets.token_urlsafe(16)


@app.context_processor
def inject_globals():
    return {
        "currency": session.get("currency", DEFAULT_CURRENCY),
        "perspective": session.get("perspective", "leslie"),
        "is_parent": session.get("piggy_parent", False),
        "guide": session.get("guide", "lorelai"),
        "plausible_domain": os.environ.get("PLAUSIBLE_DOMAIN", ""),
        "has_wage": get_effective_hourly_rate() is not None,
    }


if __name__ == "__main__":
    app.run(debug=True)
