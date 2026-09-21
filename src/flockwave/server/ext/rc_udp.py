"""Extension that accepts UDP packets on a specific port and treats them as
RC channel values for a (virtual or real) RC transmitter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from logging import Logger
from typing import TYPE_CHECKING, Any, Literal

from flockwave.connections import create_connection
from flockwave.connections.socket import UDPListenerConnection
from flockwave.networking import format_socket_address
from pydantic import BaseModel, Field
from trio import TooSlowError, fail_after

from flockwave.server.ports import suggest_port_number_for_service, use_port

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer

Decoder = Callable[[bytes], Sequence[int]]
"""Type specification for decoder functions that take a raw packet and return a
list of RC channel values
"""

SERVICE: str = "rcin"
"""Name of the service that we use to derive a default port number."""


class RcUdpConfig(BaseModel):
    """Configuration model for the RC UDP input extension."""

    host: str = Field(
        default="127.0.0.1",
        title="Host",
        description=(
            "IP address of the host that the server should listen for incoming "
            "UDP datagrams. Use an empty string to listen on all interfaces, or "
            "127.0.0.1 to listen on localhost only"
        ),
        json_schema_extra={"propertyOrder": 10},
    )

    port: int = Field(
        default=suggest_port_number_for_service(SERVICE),
        ge=1,
        le=65535,
        title="Port",
        description=(
            "Port that the server should listen on. Untick the checkbox to "
            "let the server derive the port number from its own base port."
        ),
        json_schema_extra={"propertyOrder": 20, "required": False},
    )

    bytesPerChannel: int = Field(
        default=2,
        ge=1,
        le=2,
        title="Bytes per channel",
        description="Number of bytes per channel in each UDP packet",
    )

    endianness: Literal["big", "little"] = Field(
        default="little",
        title="Endianness",
        description="Endianness of each incoming packet",
        json_schema_extra={
            "options": {
                "enum_titles": [
                    "Big endian (network byte order, MSB first)",
                    "Little endian (LSB first)",
                ]
            },
        },
    )

    range: list[int] | None = Field(
        default=None,
        title="Override channel range",
        json_schema_extra={
            "format": "table",
            "minItems": 2,
            "maxItems": 2,
            "items": {"type": "integer"},
            "required": False,
        },
    )

    timeout: float = Field(
        default=1.0,
        ge=0,
        title="Timeout",
        description=(
            "Number of seconds that must pass without receiving a UDP "
            "packet to consider the RC connection as lost. Zero means "
            "to disable the timeout."
        ),
    )


async def run(app: SkybrushServer, configuration: RcUdpConfig, log):
    host = configuration.host
    port = configuration.port
    formatted_address = format_socket_address((host, port))

    endianness = configuration.endianness
    bytes_per_channel = configuration.bytesPerChannel

    timeout = configuration.timeout
    if timeout <= 0:
        timeout = float("inf")

    value_range = parse_range(configuration.range)

    decoder = (
        decode_one_byte_per_channel
        if bytes_per_channel == 1
        else (
            decode_two_bytes_per_channel_big_endian
            if endianness == "big"
            else decode_two_bytes_per_channel_little_endian
        )
    )

    if value_range is not None:
        decoder = rescale(decoder, value_range)

    rc = app.import_api("rc")
    connection = create_connection(f"udp-listen://{host}:{port}")

    async def handler(connection):
        await handle_udp_datagrams(
            connection,
            log=log,
            address=formatted_address,
            decoder=decoder,
            on_changed=rc.notify,
            on_lost=rc.notify_lost,
            timeout=timeout,
        )

    with (
        use_port(SERVICE, port),
        app.connection_registry.use(connection, name="UDP RC input"),
    ):
        await app.supervise(connection, task=handler)


def parse_range(value: Any) -> tuple[int, int] | None:
    """Parses the 'range' parameter from the configuration and returns the
    lower and upper bound of each channel; the upper bound is exclusive.
    """
    if value is None:
        return None
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        return tuple(value)
    else:
        raise ValueError("range must be None or an array of length 2")


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


def rescale(decoder: Decoder, bounds: tuple[int, int]) -> Decoder:
    min_value, max_value = bounds
    ratio = 65535.0 / (max_value - min_value)

    def scaled_decoder(data: bytes) -> Sequence[int]:
        return [
            (
                0
                if value < min_value
                else (
                    65535
                    if value > max_value
                    else int(round(value - min_value) * ratio)
                )
            )
            for value in decoder(data)
        ]

    return scaled_decoder


async def handle_udp_datagrams(
    connection: UDPListenerConnection,
    log: Logger,
    address: str,
    decoder: Decoder,
    on_changed: Callable[[Sequence[int]], None],
    on_lost: Callable[[], None],
    timeout: float = 1,
) -> None:
    log.info(f"Listening for UDP RC input on {address}")

    connected = False

    try:
        while True:
            if connected:
                try:
                    with fail_after(timeout):
                        data, _ = await connection.read()
                except TooSlowError:
                    connected = False
                    log.warning("UDP RC input lost")
                    on_lost()
                    data = None
            else:
                data, _ = await connection.read()
                connected = True
                log.info("UDP RC connection established")

            if data:
                try:
                    channels = decoder(data)
                except Exception:
                    # probably dropping malformed packet
                    pass
                else:
                    on_changed(channels)
    finally:
        if connected:
            on_lost()
        log.info(f"UDP RC input closed on {address}")


dependencies = ("rc",)
description = "RC input source using UDP datagrams"
tags = "experimental"
schema = RcUdpConfig
