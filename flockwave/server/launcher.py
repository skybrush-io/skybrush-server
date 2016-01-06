"""Command line launcher for the Flockwave server."""

import click


@click.command()
@click.option("--debug/--no-debug", default=False,
              help="Start the server in debug mode")
@click.option("-p", "--port", default=5000,
              help="The port that the server will listen on")
def start(debug=False, port=5000):
    """Start the Flockwave server."""
    from flockwave.server.app import app, socketio
    socketio.run(app, port=port, debug=debug, use_reloader=False)


if __name__ == '__main__':
    start()
