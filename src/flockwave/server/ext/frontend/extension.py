"""Extension that adds a simple frontend index page to the Skybrush server,
served over HTTP.
"""

from quart import Blueprint

__all__ = ("load",)


blueprint = Blueprint("frontend", __name__, static_folder="static", static_url_path="/")


def load(app, configuration):
    """Loads the extension."""
    http_server = app.import_api("http_server")
    http_server.mount(blueprint, path=configuration.get("route", "/app"))
    http_server.propose_index_page("frontend.index", priority=0)


@blueprint.route("/")
async def index():
    """Returns the index page of the extension."""
    return await blueprint.send_static_file("index.html")


dependencies = ("http_server",)
