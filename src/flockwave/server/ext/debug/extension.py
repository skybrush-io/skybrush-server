from contextlib import ExitStack
from logging import Logger
from trio import sleep_forever
from typing import Optional, TYPE_CHECKING

from flockwave.server.utils import overridden

from .server import run_debug_port, setup_debugging_server

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: Optional["SkybrushServer"] = None
log: Optional[Logger] = None


async def run(app, configuration, logger):
    """Runs the extension."""
    global is_public

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
