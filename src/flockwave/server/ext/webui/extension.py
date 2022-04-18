"""Skybrush server extension that adds debugging tools and a test page to
the Skybrush server.
"""

from __future__ import annotations

import json
import threading

from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import wraps
from logging import Logger
from operator import attrgetter
from quart import abort, make_response, redirect, render_template, request, url_for
from trio import sleep_forever
from trio.lowlevel import current_root_task
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from flockwave.ext.errors import NotSupportedError
from flockwave.server.utils import overridden
from flockwave.server.utils.quart import make_blueprint

from .utils import (
    can_save_server_configuration,
    get_server_configuration_as_json,
    save_server_configuration,
)

if TYPE_CHECKING:
    from flockwave.ext.manager import ExtensionManager
    from flockwave.server.app import SkybrushServer


__all__ = ("index", "run")


blueprint = make_blueprint(
    "webui",
    __name__,
    static_folder="static",
    template_folder="templates",
    static_url_path="/static",
)

app: Optional["SkybrushServer"] = None
is_public: bool = False
log: Optional[Logger] = None


async def run(app, configuration, logger):
    """Runs the extension."""
    global is_public

    frontend = app.import_api("frontend")
    http_server = app.import_api("http_server")
    path = configuration.get("route", "/webui")
    is_public = bool(configuration.get("public"))

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger))
        stack.enter_context(http_server.mounted(blueprint, path=path))
        stack.enter_context(
            frontend.use_link_on_front_page(f"{blueprint.name}.index", "Configure")
        )
        await sleep_forever()


#############################################################################


@dataclass
class ExtensionInfo:
    name: str
    description: str = ""
    loaded: bool = False
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)

    @classmethod
    def for_extension(
        cls, name: str, ext_manager: "ExtensionManager", *, details: bool = False
    ):
        result = cls(name=name, loaded=ext_manager.is_loaded(name))
        result.description = ext_manager.get_description_of_extension(name) or ""
        result.tags = sorted(ext_manager.get_tags_of_extension(name))

        if details:
            result.dependencies = sorted(
                ext_manager.get_dependencies_of_extension(name)
            )
            result.dependents = sorted(
                ext_manager.get_reverse_dependencies_of_extension(name)
            )

        return result


#############################################################################
# Helper functions for routes


def is_debugging() -> bool:
    global app

    extension_manager = app.extension_manager if app else None
    return extension_manager is not None and extension_manager.is_loaded("debug")


def only_when_debugging(func: Callable[..., Awaitable[Any]]):
    """Decorator that can be added to a route and that enables the route if and
    only if the debug extension is enabled.
    """

    @wraps(func)
    async def decorated(*args, **kwds):
        if is_debugging():
            return await func(*args, **kwds)
        else:
            abort(404)

    return decorated


def _get_extension_by_name(name: str) -> Tuple[ExtensionInfo, "ExtensionManager"]:
    extension_manager = app.extension_manager if app else None
    if extension_manager and name in extension_manager.known_extensions:
        try:
            extension = ExtensionInfo.for_extension(
                name, extension_manager, details=True
            )
        except ModuleNotFoundError:
            extension = None
    else:
        extension = None

    if extension is None:
        abort(404)

    assert extension_manager is not None

    return extension, extension_manager


async def _configure_extension_from_request_body_if_needed(
    name: str,
) -> "ExtensionManager":
    _, extension_manager = _get_extension_by_name(name)

    data = await request.get_json()
    if data and isinstance(data, dict):
        # Configure the extension first
        config = data.get("config")
        if isinstance(config, dict):
            extension_manager.configure(name, config)

    return extension_manager


async def _to_json(
    func: Callable[..., Awaitable[Any]], *args, on_success: Any = None
) -> Dict[str, Any]:
    """Calls the given function and returns its result, wrapped in an appropriate
    JSON object. Catches any exceptions raised from the function and also wraps
    them in an appropriate JSON object.
    """
    global log

    try:
        result = await func(*args)
    except Exception as ex:
        if not isinstance(ex, NotSupportedError) and log:
            log.exception(ex)
        return {"error": str(ex)}

    return {"result": on_success} if result is None else {"result": result}


#############################################################################
# Route definitions


@blueprint.before_request
def fail_if_not_localhost() -> None:
    """Checks the environment of the current request being served and aborts
    the request with an HTTP 403 Forbidden if it is not coming from localhost.
    """
    if not is_public:
        # We need to abort the request if it is not coming from localhost or
        # if it has passed through proxy servers
        if request.remote_addr != "127.0.0.1" or len(request.access_route) > 1:
            abort(403)


@blueprint.context_processor
def inject_debug_variable() -> Dict[str, Any]:
    """Injects the `can_save_config` and `debug` variables into all template contexts."""
    return {
        "can_save_config": can_save_server_configuration(app),
        "debug": is_debugging(),
    }


@blueprint.route("/")
async def index():
    """Returns the index page of the extension."""
    return redirect(url_for(".list_extensions"))


@blueprint.route("/config", defaults={"as_attachment": False, "compact": False})
@blueprint.route("/config.json", defaults={"as_attachment": True, "compact": False})
@blueprint.route("/config/full", defaults={"as_attachment": False, "compact": False})
@blueprint.route(
    "/config/full.json", defaults={"as_attachment": True, "compact": False}
)
@blueprint.route("/config/compact", defaults={"as_attachment": False, "compact": True})
@blueprint.route(
    "/config/compact.json", defaults={"as_attachment": True, "compact": True}
)
async def get_configuration(as_attachment: bool = False, compact: bool = False):
    """Returns the current configuration of the server in JSON format."""
    if app is None:
        abort(403)

    config = get_server_configuration_as_json(app, compact=compact)
    formatted_config = json.dumps(config, indent=2, sort_keys=True)
    response = await make_response(formatted_config, 200)
    response.headers["Content-type"] = "application/json"
    if as_attachment:
        response.headers["Content-disposition"] = 'attachment; filename="config.json"'

    return response


@blueprint.route("/config/save", methods=["POST"])
async def save_configuration():
    """Saves the current configuration of the server, overwriting its configuration
    file.
    """
    if app is None:
        abort(403)

    return await _to_json(save_server_configuration, app, on_success=True)


@blueprint.route("/extensions")
async def list_extensions():
    """Returns a page that lists all the extensions currently known to the
    server and allows the user to load or unload them.
    """
    extension_manager = app.extension_manager if app else None
    extensions: List[ExtensionInfo] = []

    if extension_manager:
        for name in extension_manager.known_extensions:
            try:
                info = ExtensionInfo.for_extension(name, extension_manager)
                extensions.append(info)
            except ModuleNotFoundError:
                # The configuration somehow refers to an extension that does not
                # exist; this is okay, we just ignore it
                pass
            except Exception:
                # error while importing extension; let's log it an ignore it
                if log:
                    log.warning(f"Error while importing extension: {name!r}")

    return await render_template(
        "extensions.html.j2", title="Extensions", extensions=extensions
    )


@blueprint.route("/messages")
@only_when_debugging
async def send_messages():
    """Returns a page that allows the user to send messages to the server."""
    return await render_template("messages.html.j2", title="Messages")


@blueprint.route("/threads")
@only_when_debugging
async def list_threads():
    """Returns a page that lists all active threads in the server."""
    return await render_template(
        "threads.html.j2", threads=threading.enumerate(), title="Threads"
    )


@blueprint.route("/tasks")
@only_when_debugging
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
    extension, extension_manager = _get_extension_by_name(name)
    config = extension_manager.get_configuration_snapshot(name)
    if isinstance(config, dict):
        config.pop("enabled", None)

    schema = extension_manager.get_configuration_schema(name)
    return await render_template(
        "extension_details.html.j2",
        title=f"Extension: {name}",
        extension=extension,
        config=config,
        schema=schema,
    )


@blueprint.route("/extensions/<name>/load", methods=["POST"])
async def load_extension(name):
    """Loads the extension with the given name in response to a POST request."""
    extension_manager = await _configure_extension_from_request_body_if_needed(name)
    return await _to_json(extension_manager.load, name, on_success=True)


@blueprint.route("/extensions/<name>/unload", methods=["POST"])
async def unload_extension(name):
    """Unloads the extension with the given name in response to a POST request."""
    _, extension_manager = _get_extension_by_name(name)
    return await _to_json(extension_manager.unload, name, on_success=True)


@blueprint.route("/extensions/<name>/reload", methods=["POST"])
async def reload_extension(name):
    """Reloads the extension with the given name in response to a POST request."""
    _, extension_manager = _get_extension_by_name(name)

    async def reload():
        await _configure_extension_from_request_body_if_needed(name)
        await extension_manager.reload(name)
        return True

    return await _to_json(reload)


dependencies = ("frontend", "http_server")
description = "Adds a web-based configuration user interface to the server."
schema = {}
