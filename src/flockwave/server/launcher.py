"""Command line launcher for the Skybrush server."""

import click
import dotenv
import logging
import os
import sys
import trio
import warnings

from typing import Optional

from flockwave import logger

from .logger import log
from .utils.packaging import is_packaged
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
    "-p",
    "--port",
    metavar="PORT",
    default=None,
    help="Oveerride the base port number of the server. Takes precedence over the PORT environment variable.",
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
@click.version_option(version=__version__)
def start(
    config: str,
    port: Optional[int] = None,
    debug: bool = False,
    quiet: bool = False,
    log_style: str = "fancy",
):
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

    # Also silence informational messages from charset_normalizer and httpx
    for logger_name in ("charset_normalizer", "httpcore", "httpx"):
        log_handler = logging.getLogger(logger_name)
        log_handler.setLevel(logging.WARN)

    # Silence deprecation warnings from Trio if we are packaged until AnyIO
    # migrates to to_thread.run_sync(..., abandon_on_cancel=...)
    if is_packaged():
        warnings.filterwarnings(action="ignore", category=trio.TrioDeprecationWarning)

    # Load environment variables from .env
    dotenv.load_dotenv(verbose=debug)

    # Override port number from command line if needed
    if port is not None:
        os.environ["PORT"] = str(port)

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
