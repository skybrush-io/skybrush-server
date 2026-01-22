"""Extension that extends the Skybrush server with an HTTP server listening
on a specific port.

This server can then be used by other extensions to implement HTTP-specific
functionality such as a web-based debug page (`flockwave.ext.debug`) or a
Socket.IO-based channel.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from heapq import heapify, heappush
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flockwave.ext.manager import ExtensionManager
from flockwave.networking import can_bind_to_tcp_address, format_socket_address
from hypercorn.config import Config as HyperConfig
from hypercorn.logging import Logger
from hypercorn.trio import serve
from quart import Blueprint, Quart, abort, redirect, request, url_for
from quart_trio import QuartTrio
from trio import current_time, sleep

from flockwave.server.ports import suggest_port_number_for_service, use_port
from flockwave.server.types import Disposer
from flockwave.server.utils.networking import get_known_apps_for_port
from flockwave.server.utils.packaging import is_oxidized

from .routing import RoutingMiddleware
from .types import HTTPServerExtensionAPI

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

__all__ = ("exports", "load", "unload", "schema")

PACKAGE_NAME = __name__.rpartition(".")[0]

SERVICE: str = "http"
"""Name of the service that we use to derive a default port number."""

############################################################################

proposed_index_pages: list["ProposedIndexPage"] = []
quart_app: Quart | None = None
got_first_request: bool = False
ext_manager: ExtensionManager | None = None

############################################################################


@dataclass(order=True)
class ProposedIndexPage:
    priority: int = 0
    route: str = "/"


############################################################################


def create_app() -> RoutingMiddleware:
    """Creates the ASGI web application provided by this extension."""

    global quart_app, got_first_request

    if quart_app is not None:
        raise RuntimeError("App is already created")

    # root_path set to Path.cwd() because we won't have any resources here and
    # we want to be compatible with PyOxidizer where everything preferably
    # runs from memory
    if is_oxidized():
        cwd = str(Path.cwd())
        quart_app = app = QuartTrio(PACKAGE_NAME, instance_path=cwd, root_path=cwd)
    else:
        quart_app = app = QuartTrio(PACKAGE_NAME)

    # Track whether the first request was processed
    got_first_request = False

    @app.before_request
    async def track_first_request():
        global got_first_request
        got_first_request = True

    # Set up the default index route
    @app.route("/")
    async def index():
        index_url = get_index_url()
        if index_url:
            if request.query_string:
                index_url += "?" + request.query_string.decode("utf-8")
            return redirect(index_url)
        else:
            abort(404)

    router = RoutingMiddleware()
    router.add(app, scopes=("http", "websocket"))
    return router


def get_index_url() -> str | None:
    """Get the index URL with the highest priority that was proposed by
    other extensions.

    Returns:
        the URL of the best proposed index page or ``None`` if no index page has
        been proposed
    """
    global proposed_index_pages
    if proposed_index_pages:
        return url_for(proposed_index_pages[0].route)
    else:
        return None


def mount(
    app,
    *,
    path: str,
    scopes: Iterable[str] | None = None,
    priority: int = 0,
) -> Disposer | None:
    """Mounts the given ASGI web application or Quart blueprint at the
    given path.

    Parameters:
        app: the ASGI web application or Quart blueprint to mount
        path: the path to mount the web application or blueprint at
        scopes: when the app is an ASGI web application, specifies the ASGI
            scopes that the web application should respond to. `None` means
            to respond to all scopes. Ignored for blueprints.
        priority: the priority of the route if the app is an ASGI web
            application. Web applications with higher priorities take precedence
            over lower ones. Ignored for blueprints.

    Returns:
        a function that can be called to unmount the application. Unmounting
        of blueprints is not supported yet.
    """
    global ext_manager, exports, quart_app, got_first_request

    if isinstance(app, Blueprint):
        # Blueprints can only be registered if the app has not served its first
        # request yet
        assert quart_app is not None
        if got_first_request:
            if ext_manager:
                ext_manager.request_host_app_restart("http_server")
        else:
            quart_app.register_blueprint(app, url_prefix=path)
    else:
        router = exports["asgi_app"]
        assert router is not None
        return router.add(app, scopes=scopes, path=path, priority=priority)


@contextmanager
def mounted(app, *, path: str, scopes: Iterable[str] | None = None, priority: int = 0):
    """Context manager that mounts the given ASGI web application or Quart
    blueprint at the given path, and unmounts it when the context is exited.

    Parameters:
        app: the ASGI web application or Quart blueprint to mount
        path: the path to mount the web application or blueprint at
        scopes: when the app is an ASGI web application, specifies the ASGI
            scopes that the web application should respond to. `None` means
            to respond to all scopes. Ignored for blueprints.
        priority: the priority of the route if the app is an ASGI web
            application. Web applications with higher priorities take precedence
            over lower ones. Ignored for blueprints.
    """
    remover = mount(app, path=path, scopes=scopes, priority=priority)
    try:
        yield
    finally:
        if remover:
            remover()


def propose_index_page(route: str, priority: int = 0) -> Disposer:
    """Proposes the given route as a potential index page for the
    Skybrush server. This method can be called from the ``load()``
    functions of extensions when they want to propose one of their own
    routes as an index page. The server will select the index page with
    the highest priority when all the extensions have been loaded.

    Parameters:
        route: name of a route to propose as the index page, in the form of
            ``blueprint.route`` (e.g., ``webui.index``)
        priority: the priority of the proposed route.

    Returns:
        a function that can be called with no arguments to reovke the proposed
        index page
    """
    global proposed_index_pages

    page = ProposedIndexPage(priority=-priority, route=route)

    def disposer():
        index = proposed_index_pages.index(page)
        if index >= 0:
            proposed_index_pages[index] = proposed_index_pages[-1]
            proposed_index_pages.pop()
            heapify(proposed_index_pages)

    heappush(proposed_index_pages, page)

    return disposer


@contextmanager
def proposed_index_page(route: str, priority: int = 0):
    """Context manager that adds the given route as a potential index page for
    the Skybrush server as long as the execution is within the context, and
    revokes it when the context is exited.

    Parameters:
        route: name of a route to propose as the index page, in the form of
            ``blueprint.route`` (e.g., ``webui.index``)
        priority: the priority of the proposed route.
    """
    disposer = propose_index_page(route, priority)
    try:
        yield
    finally:
        disposer()


############################################################################


def load(app: SkybrushServer, configuration: dict[str, Any]):
    """Loads the extension."""
    global exports, ext_manager

    address = (
        configuration.get("host", "localhost"),
        configuration.get("port", suggest_port_number_for_service(SERVICE)),
    )
    ext_manager = app.extension_manager

    exports.update(address=address, asgi_app=create_app())


def unload(app: SkybrushServer):
    """Unloads the extension."""
    global exports, ext_manager, quart_app, got_first_request

    quart_app = None
    ext_manager = None
    got_first_request = False
    exports.update(address=None, asgi_app=None)


async def run(app: SkybrushServer, configuration: dict[str, Any], logger: Logger):
    global exports

    address = exports.get("address")
    if address is None:
        logger.warn("HTTP server address is not specified in configuration")
        return

    host, port = address

    # Parse startup delay from configuration. This is a horrible hack, but it
    # can be used to work around problems with certain setups where the server
    # responds to incoming HTTP requests _before_ all the extensions had a
    # chance to load. If one of these extensions wants to register a blueprint
    # on the HTTP server, the incoming request will prevent the server from
    # registering the blueprint because blueprints cannot be registered in Flask
    # after the first request. Increasing the delay gives a chance for the
    # extensions to load before the HTTP server starts responding to requests.
    maybe_startup_delay = configuration.get("startup_delay")
    startup_delay = 0.0
    if maybe_startup_delay is not None:
        try:
            startup_delay = float(maybe_startup_delay)
            if startup_delay < 0:
                raise ValueError
        except ValueError:
            logger.warn(f"Ignoring invalid startup delay: {maybe_startup_delay!r}")

    # Don't show info messages by default (unless the app is in debug mode),
    # show warnings and errors only
    server_log = logger.getChild("hypercorn")
    if not app.debug:
        server_log.setLevel(logging.WARNING)

    # Create configuration for Hypercorn
    config = HyperConfig()
    config.accesslog = server_log
    config.bind = [f"{host}:{port}"]
    config.certfile = configuration.get("certfile") or None
    config.errorlog = server_log
    config.keyfile = configuration.get("keyfile") or None
    config.use_reloader = False

    secure = bool(config.ssl_enabled)

    # Test quickly whether we can bind to the given host and port; if we cannot,
    # chances are that something else is using the port
    if not await can_bind_to_tcp_address(address):
        protocol = "HTTPS" if secure else "HTTP"
        formatted_address = format_socket_address(address)
        message = f"Cannot bind {protocol} server to {formatted_address}; is the port already in use?"
        apps = get_known_apps_for_port(port)

        if apps:
            message += "\nThe following application(s) may be using this port:\n"
            message += "\n".join(f"  - {app}" for app in apps)
            message += "\nAlternatively, you might have another instance of Skybrush Server running."

        logger.error(message, extra={"telemetry": "ignore"})
        return

    # Port seems to be available so try to start the "proper" server
    retries = 0
    max_retries = 3

    # Make sure to respect the prescribed startup delay
    await sleep(startup_delay)

    while True:
        protocol = "HTTPS" if secure else "HTTP"
        formatted_address = format_socket_address(address)
        logger.info(f"Starting {protocol} server on {formatted_address}")

        started_at = current_time()

        try:
            asgi_app = exports.get("asgi_app")
            assert asgi_app is not None
            with use_port(SERVICE, port):
                await serve(asgi_app, config)
        except Exception as ex:
            print(repr(ex))
            print(getattr(ex, "__cause__", None))
            # Server crashed -- maybe a change in IP address? Let's try again
            # if we have not reached the maximum retry count.
            if current_time() - started_at >= 5:
                retries = 0

            if retries < max_retries:
                logger.error(
                    "Server stopped unexpectedly, retrying...",
                    extra={"telemetry": "ignore"},
                )
                await sleep(1)
                retries += 1
            else:
                # Re-raise the exception; the extension manager will take care
                # of logging it nicely
                raise
        else:
            break


description = "HTTP server that listens on a specific port"

# We need the unusual typehint below only for ty
# to handle `global exports` calls properly
exports: HTTPServerExtensionAPI = {
    "address": None,
    "asgi_app": None,
    "mount": mount,
    "mounted": mounted,
    "propose_index_page": propose_index_page,
    "proposed_index_page": proposed_index_page,
}

schema = {
    "properties": {
        "host": {
            "type": "string",
            "title": "Host",
            "description": (
                "IP address of the host that the server should listen on. Use "
                "an empty string to listen on all interfaces, or 127.0.0.1 to "
                "listen on localhost only"
            ),
            "default": "127.0.0.1",
            "propertyOrder": 10,
        },
        "port": {
            "type": "integer",
            "title": "Port",
            "description": (
                "Port that the server should listen on. Untick the checkbox to "
                "let the server derive the port number from its own base port."
            ),
            "minimum": 1,
            "maximum": 65535,
            "default": suggest_port_number_for_service("http"),
            "required": False,
            "propertyOrder": 20,
        },
        "certfile": {
            "type": "string",
            "title": "Certificate file",
            "description": "Full path to the certificate file that the server should use for HTTPS connections",
            "required": False,
        },
        "keyfile": {
            "type": "string",
            "title": "Private key file",
            "description": "Full path to the private key file corresopnding to the certificate",
            "required": False,
        },
    }
}
