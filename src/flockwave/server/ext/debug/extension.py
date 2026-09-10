from __future__ import annotations

from contextlib import ExitStack
from logging import Logger
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from trio import sleep_forever

from flockwave.server.utils import overridden

from .server import run_debug_port, setup_debugging_server

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: SkybrushServer | None = None
log: Logger | None = None


class DebugConfig(BaseModel):
    """Configuration model for the debug extension."""

    host: str = Field(
        default="localhost",
        title="Host",
        description=(
            "Hostname or IP address where the debug port should be opened. "
            "Ignored if no debug port is configured."
        ),
    )
    port: int = Field(
        default=0,
        title="Debug port",
        description=(
            "Number of the TCP port to open for debugging purposes. Zero or negative "
            "numbers disable the debug port."
        ),
    )


async def run(app: SkybrushServer, configuration: DebugConfig, logger: Logger):
    """Runs the extension."""
    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=logger))

        if configuration.port > 0:
            on_message = setup_debugging_server(app, stack, debug_clients=True)
            await run_debug_port(
                configuration.host or "",
                configuration.port,
                on_message=on_message,
                log=log,
            )
        else:
            await sleep_forever()


dependencies = ()
description = "Debugging tools"
schema = DebugConfig
