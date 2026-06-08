from flask import Blueprint

# Dinaro blueprint. Routes are defined in dinaro.routes.
# Self-contained: it serves its own templates (dinaro/templates) and static
# assets (dinaro/static, e.g. its "Piggy Pop" stylesheet) so the module can
# eventually be lifted into its own deployable app.

dinaro_bp = Blueprint(
    "dinaro",
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Import routes to attach them to the blueprint
from . import routes  # noqa: E402,F401
