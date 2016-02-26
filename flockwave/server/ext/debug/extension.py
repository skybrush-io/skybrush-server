"""Flockwave server extension that adds debugging tools and a test page to
the Flockwave server.
"""

from flask import Blueprint

__all__ = ("load", "index")


blueprint = Blueprint("debug", __name__, static_folder="static")


def load(app, configuration, logger):
    """Loads the extension."""
    app.register_blueprint(blueprint,
                           url_prefix=configuration.get("route", "/"))


@blueprint.route("/")
def index():
    """Returns the index page of the extension."""
    return blueprint.send_static_file("index.html")
