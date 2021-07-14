"""Extension that implements an OSC client in Skybrush that forward the
positions of the drones to a remote OSC target.
"""

from contextlib import ExitStack
from errno import ECONNREFUSED
from functools import partial
from logging import Logger
from flockwave.channels.message import MessageChannel
from trio import sleep
from typing import Optional

from flockwave.connections import Connection, create_connection
from flockwave.server.model import ConnectionPurpose

from flockwave.server.ext.osc.message import OSCMessage

from flockwave.server.utils.generic import overridden

from .channel import create_osc_channel


log: Optional[Logger] = None


async def run(app, configuration, log: Logger):
    connection_spec = configuration.get("connection", "")
    host = configuration.get("host", "localhost")
    port = configuration.get("port", 10000)

    if not connection_spec:
        connection_spec = f"udp://{host}:{port}"

    address, _, _ = connection_spec.partition("?")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), log=log))

        connection = create_connection(connection_spec)
        stack.enter_context(
            app.connection_registry.use(
                connection,
                "osc",
                "OSC connection",
                ConnectionPurpose.other,  # type: ignore
            )
        )

        await app.supervise(connection, task=partial(run_connection, address=address))


async def run_connection(connection: Connection, address: str) -> None:
    """Task that manages a single OSC connection."""
    channel = create_osc_channel(connection)
    try:
        if log:
            log.info(f"OSC connection to {address} up and running")
        await run_channel(channel)
    except Exception as ex:
        if log:
            log.error(str(ex))
    finally:
        if log:
            log.error(f"OSC connection to {address} stopped unexpectedly")


async def run_channel(channel: MessageChannel[OSCMessage]) -> None:
    """Task that manages a single OSC message channel."""
    while True:
        message = OSCMessage(b"/ping", (123, 456))

        try:
            await channel.send(message)
        except OSError as ex:
            if ex.errno == ECONNREFUSED:
                # This is normal when using UDP, the server we are sending the
                # messages to is not up yet
                pass
            else:
                raise

        await sleep(1)
