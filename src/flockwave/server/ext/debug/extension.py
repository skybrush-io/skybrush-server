"""Flockwave server extension that adds debugging tools and a test page to
the Flockwave server.
"""

import threading

from operator import attrgetter
from quart import Blueprint, render_template
from trio.lowlevel import current_root_task

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


@blueprint.route("/tasks")
async def list_tasks():
    """Returns a page that lists all active Trio tasks in the server."""

    tasks = []
    queue = [(0, current_root_task())]
    while queue:
        level, task = queue.pop()
        tasks.append(("    " * level, task))
        for nursery in task.child_nurseries:
            queue.extend(
                (level + 1, task)
                for task in sorted(
                    nursery.child_tasks, key=attrgetter("name"), reverse=True
                )
            )

    return await render_template("tasks.html", tasks=tasks)


dependencies = ("http_server",)
