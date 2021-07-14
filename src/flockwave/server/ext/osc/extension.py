"""Extension that implements an OSC client in Skybrush that forward the
positions of the drones to a remote OSC target.
"""

from __future__ import annotations

from contextlib import ExitStack
from errno import ECONNREFUSED
from functools import partial
from logging import Logger
from time import time
from trio import sleep
from typing import cast, Generator, Optional, TYPE_CHECKING

from flockwave.channels.message import MessageChannel
from flockwave.connections import Connection, create_connection
from flockwave.server.ext.osc.message import OSCMessage
from flockwave.server.model import ConnectionPurpose, UAV
from flockwave.server.utils.generic import overridden

from .channel import create_osc_channel

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: Optional["SkybrushServer"] = None
log: Optional[Logger] = None


async def run(app: "SkybrushServer", configuration, log: Logger):
    connection_spec = configuration.get("connection", "")
    host = configuration.get("host", "localhost")
    port = configuration.get("port", 10000)
    interval = configuration.get("interval", 0.5)

    if not connection_spec:
        connection_spec = f"udp://{host}:{port}"

    address, _, _ = connection_spec.partition("?")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=log))

        connection = create_connection(connection_spec)
        stack.enter_context(
            app.connection_registry.use(
                connection,
                "osc",
                "OSC connection",
                ConnectionPurpose.other,  # type: ignore
            )
        )

        await app.supervise(
            connection, task=partial(run_connection, address=address, interval=interval)
        )


async def run_connection(
    connection: Connection, *, address: str, interval: float
) -> None:
    """Task that manages a single OSC connection.

    Parameters:
        connection: the OSC connection to manage
        address: the address of the OSC connection as a human-readable string,
            for logging purposes
        interval: number of seconds between consecutive position updates sent
            as OSC messages on the connection
    """
    channel = create_osc_channel(connection)
    try:
        if log:
            log.info(f"OSC connection to {address} up and running")
        await run_channel(channel, interval=interval)
    except Exception as ex:
        if log:
            log.error(str(ex))
    finally:
        if log:
            log.error(f"OSC connection to {address} stopped unexpectedly")


async def run_channel(channel: MessageChannel[OSCMessage], *, interval: float) -> None:
    """Task that manages a single OSC message channel.

    Parameters:
        connection: the OSC channel to manage
        interval: number of seconds between consecutive position updates sent
            as OSC messages on the channel
    """
    while True:
        try:
            for message in generate_status_messages():
                await channel.send(message)
        except OSError as ex:
            if ex.errno == ECONNREFUSED:
                # This is normal when using UDP, the server we are sending the
                # messages to is not up yet. We wait five seconds and then try
                # again
                await sleep(max(0, 5 - interval))
            else:
                raise

        await sleep(interval)


def generate_status_messages() -> Generator[OSCMessage, None, None]:
    global app

    if app is None:
        return

    # We dispatch a message for all UAVs where the status was updated in the
    # last five seconds
    threshold = time() - 5

    for uav_id in app.object_registry.ids_by_type(UAV):
        uav = cast(UAV, app.object_registry.find_by_id(uav_id))
        timestamp = uav.status.timestamp
        if timestamp > threshold:
            pos_geo = uav.status.position
            if pos_geo.amsl is not None and (pos_geo.lat != 0 or pos_geo.lon != 0):
                yield OSCMessage(
                    f"/skybrush/uavs/{uav_id}/pos/geo".encode(
                        "ascii", errors="replace"
                    ),
                    (float(pos_geo.lat), float(pos_geo.lon), float(pos_geo.amsl)),
                )

            pos_xyz = uav.status.position_xyz
            if pos_xyz:
                yield OSCMessage(
                    f"/skybrush/uavs/{uav_id}/pos/xyz".encode(
                        "ascii", errors="replace"
                    ),
                    (float(pos_xyz.x), float(pos_xyz.y), float(pos_xyz.z)),
                )
