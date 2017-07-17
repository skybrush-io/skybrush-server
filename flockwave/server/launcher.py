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
@click.option("--ssl-key", default=None, type=click.Path(exists=True),
              help="Private key of the SSL certificate in PEM format")
@click.option("--ssl-cert", default=None, type=click.Path(exists=True),
              help="SSL certificate in PEM format")
def start(debug, host, port, ssl_key, ssl_cert):
    """Start the Flockwave server."""

    # Dirty workaround for breaking import cycle according to
    # https://github.com/eventlet/eventlet/issues/394
    eventlet.sleep()

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

    # Note the lazy import; this is to ensure that the logging is set up by the
    # time we start configuring the app.
    from flockwave.server.app import app, socketio

    # Construct SSL-related parameters to socketio.run() if needed
    ssl_args = {}
    if ssl_key or ssl_cert:
        ssl_args.update({
            "keyfile": ssl_key,
            "certfile": ssl_cert
        })
        log.info("Starting secure Flockwave server on port {0}...".format(port))
    else:
        log.info("Starting Flockwave server on port {0}...".format(port))

    # Now start the server
    socketio.run(app, host=host, port=port, debug=debug, use_reloader=False,
                 log=eventlet_log, **ssl_args)


if __name__ == '__main__':
    start()
