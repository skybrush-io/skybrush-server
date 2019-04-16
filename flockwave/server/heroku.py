"""Heroku-specific routines in the Flockwave server."""

from __future__ import absolute_import

import os

from .launcher import start as plain_start

__all__ = ("start", )


def start():
    """Start the Flockwave server in a Heroku dyno."""
    # TODO(ntamas): fix this; we don't have -h and -p any more
    port = os.environ.get("PORT", 5000)
    args = ["-h", "0.0.0.0", "-p", port]
    if "DEBUG" in os.environ:
        args.append("--debug")
    return plain_start(args)


if __name__ == "__main__":
    start()
