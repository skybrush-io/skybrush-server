"""Command line launcher for the Flockwave server."""

from __future__ import absolute_import

import click
import dotenv
import logging
import sys
import trio

from flockwave import logger
from flockwave.logger import log


@click.command()
@click.option(
    "-c",
    "--config",
    type=click.Path(resolve_path=True),
    help="Name of the configuration file to load; defaults to "
    "flockwave.cfg in the current directory",
)
@click.option(
    "--debug/--no-debug", default=False, help="Start the server in debug mode"
)
def start(config, debug):
    """Start the Flockwave server."""
    # Set up the logging format
    logger.install(level=logging.DEBUG if debug else logging.INFO)

    # Also silence Engine.IO and Socket.IO when not in debug mode
    if not debug:
        for logger_name in ("engineio", "socketio"):
            log_handler = logging.getLogger(logger_name)
            log_handler.setLevel(logging.ERROR)

    # Load environment variables from .env
    dotenv.load_dotenv(verbose=True)

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
    trio.run(app.run)

    # Log that we have stopped cleanly.
    log.info("Shutdown finished.")


if __name__ == "__main__":
    sys.exit(start())
