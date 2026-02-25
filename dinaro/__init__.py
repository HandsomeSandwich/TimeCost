from flask import Blueprint

# Dinaro blueprint. Routes are defined in dinaro.routes

dinaro_bp = Blueprint("dinaro", __name__)

# Import routes to attach them to the blueprint
from . import routes  # noqa: E402,F401
