"""Command line launcher for the Flockwave server."""

from __future__ import absolute_import

import click
import eventlet
import logging
import sys

from . import logger
from .logger import log


@click.command()
@click.option("-c", "--config", type=click.Path(resolve_path=True),
              help="Name of the configuration file to load; defaults to "
                   "flockwave.cfg in the current directory")
@click.option("--debug/--no-debug", default=False,
              help="Start the server in debug mode")
def start(config, debug):
    """Start the Flockwave server."""
    # Dirty workaround for breaking import cycle according to
    # https://github.com/eventlet/eventlet/issues/394
    eventlet.sleep()

    # Ensure that everything is monkey-patched by Eventlet as soon as
    # possible
    eventlet.monkey_patch()

    # Set up the logging format
    logger.install(level=logging.DEBUG if debug else logging.INFO)

    # Also silence Engine.IO and Socket.IO when not in debug mode
    if not debug:
        for logger_name in ("engineio", "socketio"):
            log_handler = logging.getLogger(logger_name)
            log_handler.setLevel(logging.ERROR)

    # Note the lazy import; this is to ensure that the logging is set up by the
    # time we start configuring the app.
    from flockwave.server.app import app

    # Log what we are doing
    log.info("Starting Flockwave server...")

    # Configure the application
    retval = app.prepare(config)
    if retval is not None:
        return retval

    # Now start the server
    app.start()


if __name__ == '__main__':
    sys.exit(start())
