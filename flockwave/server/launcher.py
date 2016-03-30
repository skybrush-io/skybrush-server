"""Command line launcher for the Flockwave server."""

from __future__ import absolute_import

import click
import eventlet
import logging

from . import logger
from .logger import log


@click.command()
@click.option("--debug/--no-debug", default=False,
              help="Start the server in debug mode")
@click.option("-h", "--host", default="127.0.0.1",
              help="The IP address that the server will bind to")
@click.option("-p", "--port", default=5000,
              help="The port that the server will listen on")
def start(debug, host, port):
    """Start the Flockwave server."""
    # Ensure that everything is monkey-patched by Eventlet as soon as
    # possible
    eventlet.monkey_patch()

    # Set up the logging format
    logger.install(level=logging.DEBUG if debug else logging.INFO)

    # Create a child logger for Eventlet so we can silence things
    # from Eventlet by default.
    eventlet_log = log.getChild("eventlet")
    eventlet_log.setLevel(logging.INFO)

    # Also silence Engine.IO and Socket.IO when not in debug mode
    if not debug:
        for logger_name in ("engineio", "socketio"):
            log_handler = logging.getLogger(logger_name)
            log_handler.setLevel(logging.ERROR)

    # Start the SocketIO server. Note the lazy import; this is to ensure
    # that the logging is set up by the time we start configuring the app.
    from flockwave.server.app import app, socketio

    log.info("Starting Flockwave server on port {0}...".format(port))
    socketio.run(app, host=host, port=port, debug=debug, use_reloader=False,
                 log=eventlet_log)


if __name__ == '__main__':
    start()
