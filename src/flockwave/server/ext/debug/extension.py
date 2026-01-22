from __future__ import annotations

from contextlib import ExitStack
from logging import Logger
from typing import TYPE_CHECKING

from trio import sleep_forever

from flockwave.server.utils import overridden

from .server import run_debug_port, setup_debugging_server

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: SkybrushServer | None = None
log: Logger | None = None


async def run(app, configuration, logger):
    """Runs the extension."""
    host = configuration.get("host", "localhost")
    port = configuration.get("port")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger))

        if port is not None:
            on_message = setup_debugging_server(app, stack, debug_clients=True)
            await run_debug_port(host or "", port, on_message=on_message, log=log)
        else:
            await sleep_forever()


dependencies = ()
description = "Debugging tools"
schema = {}
