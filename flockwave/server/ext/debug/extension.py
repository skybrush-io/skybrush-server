"""Flockwave server extension that adds debugging tools and a test page to
the Flockwave server.
"""

import threading

from quart import Blueprint, render_template

__all__ = ("load", "index")


blueprint = Blueprint(
    "debug",
    __name__,
    static_folder="static",
    template_folder="templates",
    static_url_path="/static",
)


def load(app, configuration):
    """Loads the extension."""
    http_server = app.import_api("http_server")
    http_server.mount(blueprint, path=configuration.get("route", "/debug"))
    http_server.propose_index_page("debug.index", priority=-100)


@blueprint.route("/")
async def index():
    """Returns the index page of the extension."""
    return await render_template("index.html")


@blueprint.route("/threads")
async def list_threads():
    """Returns a page that lists all active threads in the server."""
    data = {"threads": threading.enumerate()}
    return await render_template("threads.html", **data)


dependencies = ("http_server",)
