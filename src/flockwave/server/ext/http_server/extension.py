"""Extension that extends the Flockwave server with an HTTP server listening
on a specific port.

This server can then be used by other extensions to implement HTTP-specific
functionality such as a web-based debug page (`flockwave.ext.debug`) or a
Socket.IO-based channel.
"""

from contextlib import contextmanager
from flockwave.networking import format_socket_address
from heapq import heappush
from hypercorn.config import Config as HyperConfig
from hypercorn.trio import serve
from quart import Blueprint, abort, redirect, url_for
from quart_trio import QuartTrio
from trio import current_time, sleep
from typing import Callable, Iterable, Optional

import logging

from .routing import RoutingMiddleware

__all__ = ("exports", "load", "unload")

PACKAGE_NAME = __name__.rpartition(".")[0]

############################################################################

proposed_index_pages = []
quart_app = None

############################################################################


def create_app():
    """Creates the ASGI web application provided by this extension."""

    global quart_app

    if quart_app is not None:
        raise RuntimeError("App is already created")

    quart_app = app = QuartTrio(PACKAGE_NAME)

    # Set up the default index route
    @app.route("/")
    async def index():
        index_url = get_index_url()
        if index_url:
            return redirect(index_url)
        else:
            abort(404)

    router = RoutingMiddleware()
    router.add(app, scopes=("http", "websocket"))
    return router


def get_index_url():
    """Get the index URL with the highest priority that was proposed by
    other extensions.

    Returns:
        Optional[str]: the URL of the best proposed index page or
            ``None`` if no index page has been proposed
    """
    global proposed_index_pages
    if proposed_index_pages:
        return url_for(proposed_index_pages[0][1])
    else:
        return None


def mount(
    app, *, path: str, scopes: Optional[Iterable[str]] = None, priority: int = 0
) -> Optional[Callable[[], None]]:
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
    global exports, quart_app

    if isinstance(app, Blueprint):
        quart_app.register_blueprint(app, url_prefix=path)
    else:
        return exports["asgi_app"].add(app, scopes=scopes, path=path, priority=priority)


@contextmanager
def mounted(
    app, *, path: str, scopes: Optional[Iterable[str]] = None, priority: int = 0
):
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


def propose_index_page(route, priority=0):
    """Proposes the given route as a potential index page for the
    Flockwave server. This method can be called from the ``load()``
    functions of extensions when they want to propose one of their own
    routes as an index page. The server will select the index page with
    the highest priority when all the extensions have been loaded.

    Parameters:
        route (str): name of a route to propose as the index
            page, in the form of ``blueprint.route``
            (e.g., ``debug.index``)
        priority (Optional[int]): the priority of the proposed route.
    """
    global proposed_index_pages
    heappush(proposed_index_pages, (priority, route))


############################################################################


def load(app, configuration):
    """Loads the extension."""
    global exports

    address = (
        configuration.get("host", "localhost"),
        int(configuration.get("port", 5000)),
    )

    exports.update(address=address, asgi_app=create_app())


def unload(app):
    """Unloads the extension."""
    global exports, quart_app

    quart_app = None
    exports.update(address=None, asgi_app=None)


async def run(app, configuration, logger):
    global exports

    address = exports.get("address")
    if address is None:
        logger.warn("HTTP server address is not specified in configuration")
        return

    host, port = address

    # Don't show info messages by default (unless the app is in debug mode),
    # show warnings and errors only
    server_log = logger.getChild("hypercorn")
    if not app.debug:
        server_log.setLevel(logging.WARNING)

    # Create configuration for Hypercorn
    config = HyperConfig()
    config.accesslog = server_log
    config.bind = [f"{host}:{port}"]
    config.certfile = configuration.get("certfile")
    config.errorlog = server_log
    config.keyfile = configuration.get("keyfile")
    config.use_reloader = False

    secure = bool(config.ssl_enabled)

    retries = 0
    max_retries = 3

    while True:
        logger.info(
            "Starting {1} server on {0}...".format(
                format_socket_address(address), "HTTPS" if secure else "HTTP"
            )
        )

        started_at = current_time()

        try:
            await serve(exports["asgi_app"], config)
        except Exception:
            # Server crashed -- maybe a change in IP address? Let's try again
            # if we have not reached the maximum retry count.
            if current_time() - started_at >= 5:
                retries = 0

            if retries < max_retries:
                logger.error("Server stopped unexpectedly, retrying...")
                await sleep(1)
                retries += 1
            else:
                # Re-raise the exception; the extension manager will take care
                # of logging it nicely
                raise
        else:
            break


exports = {
    "address": None,
    "asgi_app": None,
    "mount": mount,
    "mounted": mounted,
    "propose_index_page": propose_index_page,
}
