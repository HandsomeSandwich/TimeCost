from flask import Blueprint

couples_bp = Blueprint("couples", __name__)

from . import routes  # noqa: E402,F401
