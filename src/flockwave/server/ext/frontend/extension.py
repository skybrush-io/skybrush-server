"""Extension that adds a simple frontend index page to the Skybrush server,
served over HTTP.
"""

from bisect import insort_right
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List

from quart import render_template, url_for

from flockwave.server.utils.quart import make_blueprint

__all__ = ("load",)


@dataclass(frozen=True, order=True)
class FrontPageLink:
    """Class representing a link on the front page provided by this extension,
    typically leading to a subpage created and managed by another extension.
    """

    route: str = field(compare=False)
    """The route that the link leads to."""

    priority: int = 0
    """The priority of the link."""

    title: str = ""
    """The title of the link."""

    @property
    def url(self) -> str:
        """The URL that the link should point to."""
        if "://" in self.route:
            # This is an absolute URL
            return self.route
        else:
            # This is a route reference
            return url_for(self.route)


front_page_links: List[FrontPageLink] = []
"""The list of currently registered front page links."""


def load(app, configuration):
    """Loads the extension."""
    static_folder_from_config = configuration.get("path")
    has_static_folder_in_config = bool(static_folder_from_config)
    route = configuration.get("route", "/app")

    if has_static_folder_in_config:
        static_folder = str(Path(static_folder_from_config).resolve())
    else:
        static_folder = "static"

    blueprint = make_blueprint(
        "frontend",
        __name__,
        static_folder=static_folder,
        template_folder="templates",
        static_url_path="/",
    )

    @blueprint.route("/")
    async def index():
        """Returns the index page of the extension."""
        if has_static_folder_in_config:
            return await blueprint.send_static_file("index.html")
        else:
            return await render_template("index.html.j2", links=front_page_links)

    http_server = app.import_api("http_server")
    http_server.mount(blueprint, path=route)
    http_server.propose_index_page("frontend.index", priority=0)


@contextmanager
def use_link_on_front_page(
    route: str, title: str, *, priority: int = 0
) -> Iterator[FrontPageLink]:
    global front_page_links

    link = FrontPageLink(title=title, route=route, priority=priority)
    insort_right(front_page_links, link)

    try:
        yield link
    finally:
        front_page_links.remove(link)


dependencies = ("http_server",)
description = "Simple frontend index page served over HTTP"
exports = {"use_link_on_front_page": use_link_on_front_page}
schema = {}
