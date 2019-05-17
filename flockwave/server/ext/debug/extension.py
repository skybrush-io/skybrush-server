"""Flockwave server extension that adds debugging tools and a test page to
the Flockwave server.
"""

import threading

from eventlet.debug import format_hub_listeners, format_hub_timers
from flask import Blueprint, render_template

__all__ = ("load", "index")


blueprint = Blueprint(
    "debug",
    __name__,
    static_folder="static",
    template_folder="templates",
    static_url_path="/static",
)


def load(app, configuration, logger):
    """Loads the extension."""
    http_server = app.import_api("http_server")
    http_server.wsgi_app.register_blueprint(
        blueprint, url_prefix=configuration.get("route", "/")
    )
    http_server.propose_index_page("debug.index", priority=-100)


@blueprint.route("/")
def index():
    """Returns the index page of the extension."""
    return render_template("index.html")


@blueprint.route("/greenlets")
def list_greenlets():
    """Returns a page that lists all active greenlets in the current
    thread.
    """
    data = {"listeners": format_hub_listeners(), "timers": format_hub_timers()}
    return render_template("greenlets.html", **data)


@blueprint.route("/threads")
def list_threads():
    """Returns a page that lists all active threads in the server."""
    data = {"threads": threading.enumerate()}
    return render_template("threads.html", **data)


dependencies = ("http_server",)
