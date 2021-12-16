"""Extension that can connect to an external GPS receiver and show the
location data from the GPS as a beacon.

Simulated GPS data can be generated in a throwaway Docker container with:

docker run --rm -it --name=gpsd -p 2947:2947 -p 8888:8888 knowhowlab/gpsd-nmea-simulator
"""

from __future__ import annotations

from contextlib import ExitStack
from enum import Enum
from functools import partial
from json import loads
from pynmea2 import parse as parse_nmea
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from flockwave.gps.vectors import GPSCoordinate
from flockwave.channels import ParserChannel
from flockwave.channels.types import Parser
from flockwave.connections import (
    create_connection,
    Connection,
    ListenerConnection,
    RWConnection,
)
from flockwave.ext.manager import ExtensionAPIProxy
from flockwave.networking import format_socket_address
from flockwave.parsers import create_line_parser
from flockwave.server.errors import NotSupportedError
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.uav import PassiveUAVDriver
from flockwave.spec.ids import make_valid_object_id

from flockwave.server.utils.generic import overridden

from .base import UAVExtensionBase

if TYPE_CHECKING:
    from flockwave.server.app import SkybrushServer
    from flockwave.server.ext.beacon.model import Beacon

#: Type alias for the unified GPS-related message format used by this extension
GPSMessage = Dict[str, Any]


class MessageFormat(Enum):
    """Enum representing the message formats that the extension can handle."""

    AUTO = "auto"
    GPSD = "gpsd"
    NMEA = "nmea"

    def create_parser(self) -> Parser[bytes, GPSMessage]:
        if self is MessageFormat.GPSD:
            decoder = parse_incoming_gpsd_message
        elif self is MessageFormat.NMEA:
            decoder = parse_incoming_nmea_message
        elif self is MessageFormat.AUTO:
            decoder = parse_incoming_gpsd_or_nmea_message
        else:
            raise ValueError("Cannot create parser for format: {self!r}")
        return create_line_parser(decoder=decoder, min_length=1, filter=bool)


def create_gps_connection_and_format(
    connection: str, format: Optional[str] = None
) -> Tuple[Connection, MessageFormat]:
    """Creates a connection from a connection specification object found
    in the configuration of the extension. The ``connection`` and ``format``
    keys of the configuration object must be passed to this function.

    The value of ``connection`` must be:

    - ``"gpsd"``, in which case we assume that the GPS is accessible via
      ``gpsd`` on localhost at its default port (2947)

    - a string that does *not* contain a colon (`:`), in which case it is
      assumed to be the name of a serial port where the GPS is accessible
      directly

    - a string containing a colon (`:`), in which case it is assumed that
      the string is a URL and the protocol part of the string describes
      the transport being used to access the GPS (e.g., serial port, TCP
      stream or something else).

    - an object representation accepted by ``create_connection()``

    The value of ``format`` must be ``"auto"`` (autodetection), ``"gpsd"``
    (``gpsd`` protocol) or ``"nmea"`` (NMEA-0183 protocol). When ``format``
    is omitted, it defaults to ``"auto"``.

    Returns:
        (Connection, MessageFormat): an appropriately configured connection object,
            and an appropriately configured parser object that can be fed with
            raw data from the connection and that will call a callback for each
            detected message
    """
    if format is None:
        format = "auto"

    if connection == "gpsd":
        if format == "auto":
            format = "gpsd"
        connection = "tcp://localhost:2947"

    if ":" not in connection:
        connection = f"serial:{connection}"

    try:
        format_enum = MessageFormat(format)
    except Exception:
        raise NotSupportedError("{0!r} format is suported at the moment".format(format))

    return create_connection(connection), format_enum


def parse_incoming_gpsd_message(message: bytes) -> GPSMessage:
    """Parses an incoming message from a `gpsd` device and translates its
    content to a standard form that will be used by the extension.

    Parameters:
        message: a full message from `gpsd`, in JSON format

    Returns:
        a dictionary mapping keys like `device`, `position` and `heading` to the
        parsed `gpsd` device name, position data and heading (course) information
    """
    data = loads(message.decode("ascii"))
    result = {}
    if not isinstance(data, dict):
        return result

    cls = data.get("class", None)

    if cls == "VERSION":
        result.update(version=data.get("release"))
    elif cls == "TPV":
        lat, lon = data.get("lat"), data.get("lon")
        if lat is not None and lon is not None:
            result.update(
                device=data.get("device", "gpsd"),
                position=GPSCoordinate(lat=lat, lon=lon, agl=data.get("alt", 0)),
                heading=data.get("track", 0),
            )

    return result


def parse_incoming_nmea_message(message: bytes) -> GPSMessage:
    """Parses a raw incoming NMEA message and translates its content to a
    standard form that will be used by the extension.

    Parameters:
        message: a full NMEA message

    Returns:
        a dictionary mapping keys like `position` and `heading` to position data
        and heading (course) information
    """
    data = parse_nmea(message.decode("ascii"))
    result = {}

    if data.sentence_type == "RMC":
        result.update(
            position=GPSCoordinate(lat=data.latitude, lon=data.longitude),
            heading=data.true_course,
        )

    return result


def parse_incoming_gpsd_or_nmea_message(message: bytes) -> GPSMessage:
    """Parses an incoming message in either NMEA or `gpsd` format. The decision
    is made based on the first character of the message; if it starts with
    ``$`` or ``!``, it is assumed to be NMEA, otherwise it is assumed to be a
    `gpsd` message.
    """
    if message and message[0] in b"$!":
        return parse_incoming_nmea_message(message)
    else:
        return parse_incoming_gpsd_message(message)


class GPSExtension(UAVExtensionBase):
    """Extension that tracks position information received from external GPS
    devices and creates UAVs in the UAV registry corresponding to the GPS
    devices.
    """

    _beacon_api: ExtensionAPIProxy
    _connection_to_id: Dict[Connection, str]
    _device_to_beacon_id: Dict[str, str]
    _id_format: str
    _main_connection: Optional[Connection]

    app: "SkybrushServer"
    driver: PassiveUAVDriver

    def __init__(self):
        """Constructor."""
        super().__init__()
        self._id_format = None  # type: ignore

        self._connection_to_id = {}
        self._device_to_beacon_id = {}
        self._main_connection = None

    def configure(self, configuration):
        """Loads the extension."""
        self._id_format = configuration.get("id_format", "GPS:{0}")

    async def handle_gps_messages(
        self, connection: RWConnection[bytes, bytes], format: MessageFormat
    ) -> None:
        """Worker task that reads incoming messages from the given connection,
        parses them using the given parser and then processes them to update the
        status of the beacons managed by this extension.

        The connection is assumed to be open by the time this function is
        invoked.
        """
        try:
            await connection.wait_until_connected()
            if connection not in self._connection_to_id:
                self._connection_to_id[connection] = self._derive_id_for_connection(
                    connection
                )
            return await self._handle_gps_messages(connection, format.create_parser())
        except Exception as ex:
            if self.log:
                self.log.exception(ex)
        finally:
            self._connection_to_id.pop(connection, None)

    def _derive_id_for_connection(self, connection: RWConnection[bytes, bytes]):
        """Derives a (preferably permanent) connection identififer for the
        given connection, used as a prefix for GPS devices that send their
        messages through this connection.
        """
        if hasattr(connection, "address"):
            address = connection.address  # type: ignore
            if isinstance(address, tuple) and len(address) >= 2:
                return format_socket_address(address)
        return ""  # Do not use a prefix for this connection

    def _get_global_beacon_id(self, device_id: str) -> str:
        """Returns the global beacon object ID (registered in the object registry
        of the app) from the local device ID.
        """
        result = self._device_to_beacon_id.get(device_id)
        if result is None:
            result = make_valid_object_id(self._id_format.format(device_id))
            self._device_to_beacon_id[device_id] = result
        return result

    def _get_local_device_id(self, message: GPSMessage, sender: Connection) -> str:
        """Returns the local device ID from the GPS message that was sent and
        the connection that the message was received from.
        """
        device_id_in_message = message.get("device")
        sender_id = (
            self._connection_to_id.get(sender)
            if sender is not self._main_connection
            else None
        )

        if device_id_in_message:
            if sender_id:
                # Connection has its own ID so add that as a prefix to the device ID
                return f"{sender_id}:{device_id_in_message}"
            else:
                # Connection has no ID so just use the device ID
                return device_id_in_message
        else:
            if sender_id:
                # Connection has its own ID so use that
                return sender_id
            else:
                # Connection has no ID either so just make up an ID
                return "0"

    async def _handle_gps_messages(
        self, connection: RWConnection[bytes, bytes], parser: Parser[bytes, GPSMessage]
    ) -> None:
        async with ParserChannel(connection, parser) as channel:
            async for message in channel:
                if "version" in message:
                    # Ask gpsd to start streaming status data
                    await connection.write(b'?WATCH={"enable":true,"json":true}\n')
                elif "position" in message:
                    self._handle_single_gps_update(message, source=connection)

    def _handle_single_gps_update(
        self, message: GPSMessage, *, source: Connection
    ) -> None:
        """Handles a single GPS status update message."""
        device_id = self._get_local_device_id(message, source)
        beacon_id = self._get_global_beacon_id(device_id)

        beacon: Optional[Beacon] = self._beacon_api.find_by_id(beacon_id)
        if not beacon:
            beacon = self._beacon_api.add(beacon_id)
            assert beacon is not None

        beacon.update_status(
            position=message["position"], heading=message["heading"], active=True
        )

    async def run(self, app: "SkybrushServer", configuration, log):
        self._beacon_api = app.import_api("beacon")

        connection, format = create_gps_connection_and_format(
            connection=configuration.get("connection", "gpsd"),
        )

        address = None
        if hasattr(connection, "address"):
            address = format_socket_address(connection.address)  # type: ignore

        is_listener = isinstance(connection, ListenerConnection)

        if is_listener:
            if address:
                log.info(f"Listening for incoming GPS connections on {address}")
            else:
                log.info("Listening for incoming GPS connections")

        with ExitStack() as stack:
            stack.enter_context(overridden(self, _main_connection=connection))
            stack.enter_context(
                app.connection_registry.use(
                    connection,
                    "GPS",
                    "GPS listener"
                    if isinstance(connection, ListenerConnection)
                    else "GPS link",
                    purpose=ConnectionPurpose.gps,  # type: ignore
                )
            )
            await app.supervise(
                connection, task=partial(self.handle_gps_messages, format=format)
            )


construct = GPSExtension
dependencies = ("beacon",)
description = "External GPS receivers as beacons"
schema = {
    "properties": {
        "connection": {
            "type": "string",
            "title": "Connection URL",
            "description": (
                "Use gpsd to connect to the local gpsd daemon; alternatively, "
                "use the full name or path of a local serial port, or any "
                "valid connection URL for more advanced cases"
            ),
        },
        "id_format": {
            "type": "string",
            "default": "BEACON:{0}",
            "title": "ID format",
            "description": (
                "Python format string that determines the format of the IDs of "
                "the GPS beacons created by this extension."
            ),
        },
    }
}
