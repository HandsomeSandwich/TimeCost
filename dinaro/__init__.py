import os

from flask import Blueprint, url_for

# Dinaro blueprint. Routes are defined in dinaro.routes.
# Self-contained: it serves its own templates (dinaro/templates) and static
# assets (dinaro/static, e.g. its "Piggy Pop" stylesheet) so the module can
# eventually be lifted into its own deployable app.

dinaro_bp = Blueprint(
    "dinaro",
    __name__,
    template_folder="templates",
    static_folder="static",
    # Distinct from the app's own "/static" so the two never collide when Dinaro
    # is mounted at the root (standalone). In the main app this resolves under
    # the /dinaro prefix (/dinaro/assets/...); standalone it's /assets/...
    static_url_path="/assets",
)


@dinaro_bp.context_processor
def _inject_dinaro_globals():
    """Link back to TimeCost when mounted in the main app; degrade gracefully
    when Dinaro runs standalone (no 'core' blueprint). Set TIMECOST_URL to an
    external link in standalone deployments, or leave it unset to hide it."""
    try:
        timecost_url = url_for("core.landing")
    except Exception:
        timecost_url = os.environ.get("TIMECOST_URL", "")
    return {"timecost_url": timecost_url}


# Import routes to attach them to the blueprint
from . import routes  # noqa: E402,F401
