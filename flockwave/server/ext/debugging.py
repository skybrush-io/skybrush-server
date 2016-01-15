"""Flockwave server extension that adds debugging tools and a test page to
the Flockwave server.
"""

from flask import current_app

__all__ = ()

log = None


def load(app, configuration, logger):
    global log
    log = logger
    app.add_url_rule("/", "index", index)
    pass


def unload():
    log.info("Unloaded!")


def index():
    return current_app.send_static_file("index.html")
