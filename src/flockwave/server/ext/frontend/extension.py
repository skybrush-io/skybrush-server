"""Extension that adds a simple frontend index page to the Skybrush server,
served over HTTP.
"""

from pathlib import Path
from quart import Blueprint

__all__ = ("load",)


def load(app, configuration):
    """Loads the extension."""
    path = configuration.get("path")
    route = configuration.get("route", "/app")

    if path:
        path = str(Path(path).resolve())
    else:
        path = "static"

    blueprint = Blueprint("frontend", __name__, static_folder=path, static_url_path="/")

    @blueprint.route("/")
    async def index():
        """Returns the index page of the extension."""
        return await blueprint.send_static_file("index.html")

    http_server = app.import_api("http_server")
    http_server.mount(blueprint, path=route)
    http_server.propose_index_page("frontend.index", priority=0)


dependencies = ("http_server",)
description = "Simple frontend index page serve over HTTP"
