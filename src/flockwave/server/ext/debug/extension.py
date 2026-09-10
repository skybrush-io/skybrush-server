from __future__ import annotations

from contextlib import AsyncExitStack
from functools import partial
from logging import Logger
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from trio import open_nursery, sleep_forever

from .server import run_debug_port, setup_debugging_server

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer
    from flockwave.server.message_hub import MessageHub
    from flockwave.server.model import Client, FlockwaveMessage


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


async def handle_debug_command(
    app: SkybrushServer, message: FlockwaveMessage, sender: Client, hub: MessageHub
):
    """Handles a debug command message."""
    # Default implementation simply acknowledges the message. Replace the implementation
    # according to your ad-hoc debugging needs.
    return hub.acknowledge(message)


async def run(app: SkybrushServer, configuration: DebugConfig, logger: Logger):
    """Runs the extension."""
    async with AsyncExitStack() as stack:
        nursery = await stack.enter_async_context(open_nursery())

        # Start debugging server if needed
        if configuration.port > 0:
            on_message = setup_debugging_server(app, stack, debug_clients=True)
            nursery.start_soon(
                partial(
                    run_debug_port,
                    configuration.host or "",
                    configuration.port,
                    on_message=on_message,
                    log=logger,
                ),
            )

        # Register debug command handler - no specific purpose, can be used for ad-hoc
        # debugging in the server internals
        stack.enter_context(
            app.message_hub.use_message_handlers(
                {"X-DBG-CMD": partial(handle_debug_command, app)}
            )
        )

        await sleep_forever()


dependencies = ()
description = "Debugging tools"
schema = DebugConfig
