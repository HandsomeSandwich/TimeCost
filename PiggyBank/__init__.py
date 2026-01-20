from flask import Blueprint

piggybank_bp = Blueprint(
    "piggybank",
    __name__,
    template_folder="templates",
    static_folder="static",
)
