"""Command line launcher for the Flockwave server."""

from __future__ import absolute_import

import click
import logging

from . import logger
from .logger import log


@click.command()
@click.option("--debug/--no-debug", default=False,
              help="Start the server in debug mode")
@click.option("-p", "--port", default=5000,
              help="The port that the server will listen on")
def start(debug=False, port=5000):
    """Start the Flockwave server."""
    from flockwave.server.app import app, socketio

    # Create a child logger for Eventlet so we can silence things
    # from Eventlet by default
    eventlet_log = log.getChild("eventlet")
    eventlet_log.setLevel(logging.ERROR)

    # Set up the logging format
    logger.install(level=logging.DEBUG if debug else logging.INFO)

    # Start the SocketIO server
    log.info("Starting Flockwave server on port {0}...".format(port))
    socketio.run(app, port=port, debug=debug, use_reloader=False,
                 log=eventlet_log)


if __name__ == '__main__':
    start()
