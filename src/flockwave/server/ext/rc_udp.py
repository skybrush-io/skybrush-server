"""Extension that accepts UDP packets on a specific port and treats them as
RC channel values for a (virtual or real) RC transmitter.
"""

from __future__ import annotations

from functools import partial
from logging import Logger
from typing import Callable, Sequence, TYPE_CHECKING

from flockwave.connections import create_connection
from flockwave.connections.socket import UDPListenerConnection
from flockwave.networking import format_socket_address
from flockwave.server.ports import get_port_number_for_service

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer


async def run(app: SkybrushServer, configuration, log):
    host = configuration.get("host", "127.0.0.1")
    port = configuration.get("port", get_port_number_for_service("rcin"))
    formatted_address = format_socket_address((host, port))

    endianness = str(configuration.get("endianness", "big")).lower()
    bytes_per_channel = int(configuration.get("bytesPerChannel", 2))
    if endianness not in ("big", "little"):
        log.error("Endianness must be 'big' or 'little', disabling extension")
        return
    if bytes_per_channel != 1 and bytes_per_channel != 2:
        log.error("Bytes per RC channel must be 1 or 2, disabling extension")
        return
    decoder = (
        decode_one_byte_per_channel
        if bytes_per_channel == 1
        else decode_two_bytes_per_channel_big_endian
        if endianness == "big"
        else decode_two_bytes_per_channel_little_endian
    )

    rc = app.import_api("rc")
    connection = create_connection(f"udp-listen://{host}:{port}")

    with app.connection_registry.use(connection, name="UDP RC input"):
        await app.supervise(
            connection,
            task=partial(
                handle_udp_datagrams,
                log=log,
                address=formatted_address,
                decoder=decoder,
                on_changed=rc.notify,
            ),
        )


def decode_one_byte_per_channel(data: bytes) -> Sequence[int]:
    """Decoder function for incoming datagrams when we have one byte per channel."""
    return [x * 257 for x in data]


def decode_two_bytes_per_channel_big_endian(data: bytes) -> Sequence[int]:
    """Decoder function for incoming datagrams when we have two bytes per channel
    and each channel is big endian.
    """
    num_channels = len(data) // 2
    result = [0] * num_channels
    for i in range(num_channels):
        result[i] = (data[2 * i] << 8) + data[2 * i + 1]
    return result


def decode_two_bytes_per_channel_little_endian(data: bytes) -> Sequence[int]:
    """Decoder function for incoming datagrams when we have two bytes per channel
    and each channel is little endian.
    """
    num_channels = len(data) // 2
    result = [0] * num_channels
    for i in range(num_channels):
        result[i] = (data[2 * i + 1] << 8) + data[2 * i]
    return result


async def handle_udp_datagrams(
    connection: UDPListenerConnection,
    log: Logger,
    address: str,
    decoder: Callable[[bytes], Sequence[int]],
    on_changed: Callable[[Sequence[int]], None],
) -> None:
    log.info(f"Listening for UDP RC input on {address}")

    try:
        while True:
            data, _ = await connection.read()
            try:
                channels = decoder(data)
            except Exception:
                # probably dropping malformed packet
                pass
            else:
                on_changed(channels)
    finally:
        log.info(f"UDP RC input closed on {address}")


dependencies = ("rc",)
description = "RC input source using UDP datagrams"
schema = {
    "properties": {
        "host": {
            "type": "string",
            "title": "Host",
            "description": (
                "IP address of the host that the server should listen for incoming "
                "UDP datagrams. Use an empty string to listen on all interfaces, or "
                "127.0.0.1 to listen on localhost only"
            ),
            "default": "127.0.0.1",
            "propertyOrder": 10,
        },
        "port": {
            "type": "integer",
            "title": "Port",
            "description": (
                "Port that the server should listen on. Untick the checkbox to "
                "let the server derive the port number from its own base port."
            ),
            "minimum": 1,
            "maximum": 65535,
            "default": get_port_number_for_service("rcin"),
            "required": False,
            "propertyOrder": 20,
        },
        "bytesPerChannel": {
            "type": "integer",
            "title": "Bytes per channel",
            "minimum": 1,
            "maximum": 2,
            "default": 2,
            "description": "Number of bytes per channel in each UDP packet",
        },
        "endianness": {
            "type": "string",
            "title": "Endianness",
            "description": "Endianness of each incoming packet",
            "default": "big",
            "enum": ["big", "little"],
            "options": {
                "enum_titles": [
                    "Big endian (network byte order, MSB first)",
                    "Little endian (LSB first)",
                ]
            },
        },
    }
}
