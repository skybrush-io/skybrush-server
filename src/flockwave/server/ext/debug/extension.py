"""Skybrush server extension that adds debugging tools and a test page to
the Skybrush server.
"""

from __future__ import annotations

import threading

from contextlib import ExitStack
from dataclasses import dataclass, field
from logging import Logger
from operator import attrgetter
from quart import abort, Blueprint, render_template
from trio import sleep_forever
from trio.lowlevel import current_root_task
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

from flockwave.server.utils import overridden

from .server import run_debug_port, setup_debugging_server

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer
    from flockwave.ext.manager import ExtensionManager


__all__ = ("index", "run")


blueprint = Blueprint(
    "debug",
    __name__,
    static_folder="static",
    template_folder="templates",
    static_url_path="/static",
)

app: Optional["SkybrushServer"] = None
log: Optional[Logger] = None


async def run(app, configuration, logger):
    """Runs the extension."""
    http_server = app.import_api("http_server")
    path = configuration.get("route", "/debug")
    host = configuration.get("host", "localhost")
    port = configuration.get("port")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger))
        stack.enter_context(http_server.mounted(blueprint, path=path))
        stack.enter_context(
            http_server.proposed_index_page("debug.index", priority=-100)
        )

        if port is not None:
            on_message = setup_debugging_server(app, stack, debug_clients=True)

            # (host or None) is needed below because an empty string as the
            # hostname is not okay on Linux
            await run_debug_port(host or "", port, on_message=on_message, log=log)
        else:
            await sleep_forever()


#############################################################################


@dataclass
class ExtensionInfo:
    name: str
    description: str = ""
    loaded: bool = False
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)

    @classmethod
    def for_extension(
        cls, name: str, ext_manager: "ExtensionManager", *, details: bool = False
    ):
        result = cls(name=name, loaded=ext_manager.is_loaded(name))
        result.description = ext_manager.get_description_of_extension(name) or ""

        if details:
            result.dependencies = sorted(
                ext_manager.get_dependencies_of_extension(name)
            )
            result.dependents = sorted(
                ext_manager.get_reverse_dependencies_of_extension(name)
            )

        return result


#############################################################################
# Functions related to handling the dedicated debug port


@blueprint.route("/")
async def index():
    """Returns the index page of the extension."""
    return await render_template("index.html.j2", title="Messages")


@blueprint.route("/extensions")
async def list_extensions():
    """Returns a page that lists all the extensions currently known to the
    server and allows the user to load or unload them.
    """
    extension_manager = app.extension_manager if app else None
    extensions: List[ExtensionInfo] = []

    if extension_manager:
        for name in extension_manager.known_extensions:
            info = ExtensionInfo.for_extension(name, extension_manager)
            extensions.append(info)

    return await render_template(
        "extensions.html.j2", title="Extensions", extensions=extensions
    )


@blueprint.route("/threads")
async def list_threads():
    """Returns a page that lists all active threads in the server."""
    return await render_template(
        "threads.html.j2", threads=threading.enumerate(), title="Threads"
    )


@blueprint.route("/tasks")
async def list_tasks():
    """Returns a page that lists all active Trio tasks in the server."""

    tasks: List[Tuple[str, Any]] = []
    queue: List[Tuple[int, Any]] = [(0, current_root_task())]
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

    return await render_template("tasks.html.j2", title="Tasks", tasks=tasks)


@blueprint.route("/extensions/<name>")
async def show_extension_details(name):
    """Returns a page that shows the details and configuration of an extension
    of the server.
    """
    extension_manager = app.extension_manager if app else None
    if extension_manager and name in extension_manager.known_extensions:
        extension = ExtensionInfo.for_extension(name, extension_manager, details=True)
    else:
        extension = None

    if extension is None:
        abort(404)

    assert extension_manager is not None

    config = extension_manager.get_configuration_snapshot(name)
    if isinstance(config, dict):
        config.pop("enabled", None)

    return await render_template(
        "extension_details.html.j2",
        title=f"Extension: {name}",
        extension=extension,
        config=config,
    )


dependencies = ("http_server", "signals")
description = "Debugging tools"
