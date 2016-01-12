"""Heroku-specific routines in the Flockwave server."""

from __future__ import absolute_import

import os

from .launcher import start as plain_start

__all__ = ("start", )


def start():
    """Start the Flockwave server in a Heroku dyno."""
    port = os.environ.get("PORT", 5000)
    return plain_start(["-p", port])


if __name__ == "__main__":
    start()
