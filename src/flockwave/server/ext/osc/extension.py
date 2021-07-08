"""Extension that implements an OSC client in Skybrush that forward the
positions of the drones to a remote OSC target.
"""

from contextlib import ExitStack
from logging import Logger
from trio import sleep

from flockwave.connections import Connection, create_connection
from flockwave.server.model import ConnectionPurpose

from flockwave.server.ext.osc.message import OSCMessage

from .channel import create_osc_channel


async def run(app, configuration, log: Logger):
    with ExitStack() as stack:
        log.info("OSC extension running")

        connection = create_connection("udp://localhost:12345")
        stack.enter_context(
            app.connection_registry.use(
                connection,
                "osc",
                "OSC connection",
                ConnectionPurpose.other,  # type: ignore
            )
        )

        await app.supervise(connection, task=run_connection)


async def run_connection(connection: Connection) -> None:
    """Task that manages a single OSC connection."""
    channel = create_osc_channel(connection)

    while True:
        message = OSCMessage(b"/ping", (123, 456))
        await channel.send(message)
        await sleep(1)
