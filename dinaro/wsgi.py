"""Standalone WSGI entrypoint for Dinaro.

Runs Dinaro as its own Flask app at the domain root:

    gunicorn dinaro.wsgi:app

The very same dinaro_bp is still mounted at /dinaro inside the main TimeCost
app (app.py) — this module just proves Dinaro is liftable on its own.

Configuration (env):
  DINARO_DATABASE_URL  give Dinaro its own database (else shares the main app's)
  FLASK_SECRET_KEY     session signing key
  TIMECOST_URL         optional external link back to TimeCost (else hidden)
"""
from __future__ import annotations

import os

from flask import Flask

from dinaro import dinaro_bp
from dinaro.db import init_dinaro_db


def create_app() -> Flask:
    app = Flask(
        __name__,
        # Shared assets (favicon, manifest, sw.js, dinaro-push.js) live in the
        # repo's top-level static/. When Dinaro moves to its own repo, copy the
        # handful it uses into dinaro/static and drop this override.
        static_folder=os.path.join(os.path.dirname(__file__), os.pardir, "static"),
    )
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

    # Mounted at the root (its own domain), not under /dinaro.
    app.register_blueprint(dinaro_bp)

    try:
        init_dinaro_db()
        from dinaro.routes import _dinaro_ensure_family_codes
        _dinaro_ensure_family_codes()
    except Exception as e:  # pragma: no cover
        print("Dinaro DB init error:", e)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5060)
