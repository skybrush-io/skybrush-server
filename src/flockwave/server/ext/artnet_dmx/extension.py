"""Experimental, proof-of-concept ArtNet gateway that forwards ArtNet DMX
messages to a drone swarm based on a mapping from channels to drones.
"""

from colour import Color
from contextlib import ExitStack
from functools import partial
from inspect import iscoroutinefunction
from logging import Logger
from trio import Cancelled
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from flockwave.connections import (
    IPAddressAndPort,
    ReadableConnection,
    create_connection,
)
from flockwave.server.model import ConnectionPurpose
from flockwave.server.utils.generic import overridden

from flockwave.server.ext.artnet_dmx.packets import ArtDMXPayload, ArtNetOpCode

from flockwave.server.ext.artnet_dmx.mapping import (
    DMXFixtureType,
    DMXMapping,
    DMXMappingEntry,
)

from .parser import ArtNetParser

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

app: Optional["SkybrushServer"] = None
log: Optional[Logger] = None


async def run(app: "SkybrushServer", configuration, log: Logger):
    connection_spec = configuration.get("connection", "")
    host = configuration.get("host", "")
    port = configuration.get("port", 6454)

    if not connection_spec:
        connection_spec = f"udp-listen://{host}:{port}"

    address, _, _ = connection_spec.partition("?")

    with ExitStack() as stack:
        stack.enter_context(overridden(globals(), app=app, log=log))

        connection = create_connection(connection_spec)
        stack.enter_context(
            app.connection_registry.use(
                connection,
                "artnet",
                "ArtNet listener",
                ConnectionPurpose.other,  # type: ignore
            )
        )

        await app.supervise(connection, task=partial(run_connection, address=address))


async def run_connection(
    connection: ReadableConnection[Tuple[bytes, IPAddressAndPort]], *, address: str
) -> None:
    """Task that manages a single ArtNet listener connection.

    Parameters:
        connection: the ArtNet connection to manage
        address: the address of the ArtNet connection as a human-readable string,
            for logging purposes
    """
    global app

    assert app is not None

    mapping = DMXMapping(
        [
            DMXMappingEntry(num_fixtures=1, uav_id_getter=lambda x: "101"),
        ]
    )

    parser = ArtNetParser()
    cancelled = False
    state_by_uav_id: Dict[str, Tuple[DMXFixtureType, List[int], Color]] = {}
    changed_uav_ids: Set[str] = set()

    try:
        if log:
            log.info(
                f"ArtNet listener on {address} up and running",
                extra={"semantics": "success"},
            )

        while True:
            data, _ = await connection.read()
            packet = parser(data)
            if packet is None or packet.version < 14:
                # Probably not an ArtNet packet
                continue

            if packet.opcode != ArtNetOpCode.ARTDMX:
                # We only care about ArtDMX packets
                continue

            payload = ArtDMXPayload.from_bytes(packet.payload)
            entries = mapping.get_channel_map_for_universe(payload.universe)

            changed_uav_ids.clear()
            for index, (entry, value) in enumerate(zip(entries, payload.data)):
                if entry is None:
                    continue

                relative_index = index - entry.start_channel
                fixture_index, channel_index = divmod(
                    relative_index, entry.fixture_model.num_channels
                )

                uav_id = entry.uav_id_getter(fixture_index)
                maybe_current_state = state_by_uav_id.get(uav_id)
                if maybe_current_state is None:
                    current_state = (
                        entry.fixture_model,
                        [0] * entry.fixture_model.num_channels,
                        Color(),
                    )
                    state_by_uav_id[uav_id] = current_state
                else:
                    current_state = maybe_current_state
                current_state[1][channel_index] = value
                changed_uav_ids.add(uav_id)

            if changed_uav_ids:
                for uav_id in changed_uav_ids:
                    uav = app.find_uav_by_id(uav_id)
                    func = getattr(uav, "set_led_color", None) if uav else None
                    if callable(func):
                        fixture_model, channels, color = state_by_uav_id[uav_id]
                        fixture_model.update_color_from_channels(color, channels)
                        if iscoroutinefunction(func):
                            await func(color)
                        else:
                            func(color)

    except Exception as ex:
        if log:
            log.error(str(ex))
    except Cancelled:
        cancelled = True
        raise
    finally:
        if log:
            if cancelled:
                log.info(f"ArtNet listener on {address} closed")
            else:
                log.error(f"ArtNet listener on {address} stopped unexpectedly")


description = "ArtNet DMX gateway to control LED lights on UAVs from a DMX console"
