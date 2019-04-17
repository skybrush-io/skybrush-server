"""Extension that extends the Flockwave server with an HTTP server listening
on a specific port.

This server can then be used by other extensions to implement HTTP-specific
functionality such as a web-based debug page (`flockwave.ext.debug`) or a
Socket.IO-based channel.
"""

from eventlet import listen, spawn, wrap_ssl, wsgi
from flask import abort, Flask, redirect, url_for
from flockwave.server.authentication import jwt_authentication
from flockwave.server.networking import format_socket_address
from heapq import heappush

import logging

__all__ = ("exports", "load", "unload")

PACKAGE_NAME = __name__.rpartition(".")[0]

############################################################################

log = None
proposed_index_pages = []
ssl_params = {}


############################################################################

def create_app():
    """Creates the Flask application provided by this extension."""

    flask_app = Flask(PACKAGE_NAME)

    # Disable default JWT auth rule
    flask_app.config["JWT_AUTH_URL_RULE"] = None
    jwt_authentication.init_app(flask_app)

    # Set up the default index route
    @flask_app.route("/")
    def index():
        index_url = get_index_url()
        if index_url:
            return redirect(index_url)
        else:
            abort(404)

    return flask_app


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


def propose_index_page(route, priority=0):
    """Proposes the given Flask route as a potential index page for the
    Flockwave server. This method can be called from the ``load()``
    functions of extensions when they want to propose one of their own
    routes as an index page. The server will select the index page with
    the highest priority when all the extensions have been loaded.

    Parameters:
        route (str): name of a Flask route to propose as the index
            page, in the form of ``blueprint.route``
            (e.g., ``debug.index``)
        priority (Optional[int]): the priority of the proposed route.
    """
    global proposed_index_pages
    heappush(proposed_index_pages, (priority, route))


def start_server(app):
    """Startup hook function that will be registered in the application so
    that it will be called at the end of the startup process. Starts the HTTP
    server in a separate green thread.
    """
    global exports
    global log

    address = exports.get("address")
    if address is None:
        log.warn("HTTP server address is not specified in configuration")
        return

    sock = listen(address)
    if ssl_params:
        sock = wrap_ssl(sock, server_side=True, **ssl_params)
        secure = True
    else:
        secure = False

    # Don't show info messages by default (unless the app is in debug mode),
    # show warnings and errors only
    server_log = log.getChild("wsgi_server")
    if not app.debug:
        server_log.setLevel(logging.WARNING)

    log.info("Starting {1} server on {0}...".format(
        format_socket_address(address),
        "HTTPS" if secure else "HTTP"
    ))

    spawn(wsgi.server, sock, exports["wsgi_app"], debug=app.debug,
          log=server_log)


############################################################################

def load(app, configuration, logger):
    """Loads the extension."""
    global exports
    global log

    address = (
        configuration.get("host", "localhost"), configuration.get("port", 5000)
    )
    log = logger
    wsgi_app = create_app()

    if "keyfile" in configuration and "certfile" in configuration:
        ssl_params["keyfile"] = configuration["keyfile"]
        ssl_params["certfile"] = configuration["certfile"]

    app.register_startup_hook(start_server)

    exports.update(
        address=address,
        wsgi_app=wsgi_app
    )


def unload(app):
    """Unloads the extension."""
    global exports
    global log

    app.unregister_startup_hook(start_server)

    exports.update(
        address=None,
        wsgi_app=None
    )

    log = None


exports = {
    "address": None,
    "propose_index_page": propose_index_page,
    "wsgi_app": None
}
