"""Command line launcher for the Skybrush server."""

import click
import dotenv
import logging
import sys
import trio

from flockwave import logger

from .logger import log
from .version import __version__


@click.command()
@click.option(
    "-c",
    "--config",
    type=click.Path(resolve_path=True),
    help="Name of the configuration file to load; defaults to "
    "skybrush.cfg in the current directory",
)
@click.option(
    "-d", "--debug/--no-debug", default=False, help="Start the server in debug mode"
)
@click.option(
    "-q", "--quiet/--no-quiet", default=False, help="Start the server in quiet mode"
)
@click.option(
    "--log-style",
    type=click.Choice(["fancy", "plain", "json"]),
    default="fancy",
    help="Specify the style of the logging output",
)
def start(config, debug, quiet, log_style):
    """Start the Skybrush server."""
    # Set up the logging format
    logger.install(
        level=logging.DEBUG if debug else logging.WARN if quiet else logging.INFO,
        style=log_style,
    )

    # Silence Engine.IO and Socket.IO debug messages, debug messages from
    # Paramiko and warnings from urllib3.connectionpool
    for logger_name in (
        "engineio",
        "engineio.server",
        "socketio",
        "socketio.server",
        "paramiko",
        "urllib3.connectionpool",
    ):
        log_handler = logging.getLogger(logger_name)
        log_handler.setLevel(logging.ERROR)

    # Also silence informational messages from charset_normalizer
    for logger_name in ("charset_normalizer",):
        log_handler = logging.getLogger(logger_name)
        log_handler.setLevel(logging.WARN)

    # Load environment variables from .env
    dotenv.load_dotenv(verbose=debug)

    # Note the lazy import; this is to ensure that the logging is set up by the
    # time we start configuring the app.
    from flockwave.server.app import app

    # Log what we are doing
    log.info(f"Starting Skybrush server {__version__}")

    # Configure the application
    retval = app.prepare(config, debug=debug)
    if retval is not None:
        return retval

    # Now start the server
    trio.run(app.run)

    # Log that we have stopped cleanly.
    log.info("Shutdown finished")


if __name__ == "__main__":
    sys.exit(start(prog_name="skybrushd"))
