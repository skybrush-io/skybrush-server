"""Extension that can connect to an external GPS receiver and show the
location data from the GPS as a beacon.

Simulated GPS data can be generated in a throwaway Docker container with:

docker run --rm -it --name=gpsd -p 2947:2947 -p 8888:8888 knowhowlab/gpsd-nmea-simulator
"""

from contextlib import ExitStack
from functools import partial
from pynmea2 import parse as parse_nmea

from flockwave.gps.vectors import GPSCoordinate
from flockwave.channels import ParserChannel
from flockwave.connections import create_connection, Connection
from flockwave.parsers import LineParser
from flockwave.encoders import JSONEncoder
from flockwave.server.errors import NotSupportedError
from flockwave.server.model import ConnectionPurpose
from flockwave.server.model.uav import PassiveUAVDriver
from flockwave.spec.ids import make_valid_uav_id

from .base import UAVExtensionBase

decode_json = JSONEncoder().loads


def create_gps_connection(connection, format=None):
    """Creates a connection from a connection specification object found
    in the configuration of the extension. THe ``connection`` and ``format``
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

    The value of ``format`` must be ``"gpsd"`` or ``"nmea"``; the former
    means the ``gpsd`` protocol, while the latter means NMEA-0183.
    ``format`` may also be ``None`` or ``auto``, in which case it will
    be set to ``gpsd`` for TCP connections and ``nmea`` otherwise.

    Returns:
        (Connection, Parser): an appropriately configured connection object,
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

    if format == "auto":
        if connection.startswith("tcp:"):
            format = "gpsd"
        else:
            format = "nmea"

    if format == "gpsd":
        parser = LineParser(
            decoder=parse_incoming_gpsd_message, min_length=1, filter=bool
        )
    elif format == "nmea":
        parser = LineParser(
            decoder=parse_incoming_nmea_message, min_length=1, filter=bool
        )
    else:
        raise NotSupportedError("{0!r} format is suported at the moment".format(format))

    return create_connection(connection), parser


def parse_incoming_gpsd_message(message):
    """Parses an incoming message from a `gpsd` device and translates its
    content to a standard form that will be used by the extension.

    Parameters:
        message (bytes): a full message from `gpsd`, in JSON format

    Returns:
        dict: a dictionary mapping keys like `device`, `position`, `heading`
            to the parsed `gpsd` device name, position data and heading
            (course) information
    """
    data = decode_json(message)
    cls = data.get("class", None)
    result = {}

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


def parse_incoming_nmea_message(message):
    """Parses a raw incoming NMEA message and translates its content to a
    standard form that will be used by the extension.

    Parameters:
        message (bytes): a full NMEA message

    Returns:
        dict: a dictionary mapping keys like `position`, `heading` to position
            data and heading (course) information
    """
    data = parse_nmea(message.decode("ascii"))
    result = {}

    if data.sentence_type == "RMC":
        result.update(
            device="0",
            position=GPSCoordinate(lat=data.latitude, lon=data.longitude),
            heading=data.true_course,
        )

    return result


class GPSExtension(UAVExtensionBase):
    """Extension that tracks position information received from external GPS
    devices and creates UAVs in the UAV registry corresponding to the GPS
    devices.
    """

    def __init__(self):
        """Constructor."""
        super(GPSExtension, self).__init__()
        self._id_format = None
        self._device_to_uav_id = {}

    def _create_driver(self):
        return PassiveUAVDriver()

    def configure(self, configuration):
        """Loads the extension."""
        self._id_format = configuration.get("id_format", "GPS:{0}")

    async def handle_gps_messages(self, connection: Connection, parser):
        """Worker task that reads incoming messages from the given connection,
        parses them using the given parser and then processes them to update the
        status of the beacons managed by this extension.

        The connection is assumed to be open by the time this function is
        invoked.
        """
        await connection.wait_until_connected()

        async with ParserChannel(connection, parser) as channel:
            async for message in channel:
                if "version" in message:
                    # Ask gpsd to start streaming status data
                    await connection.write(b'?WATCH={"enable":true,"json":true}\n')
                elif "device" in message:
                    self._handle_single_gps_update(message)

    def _handle_single_gps_update(self, message):
        uav_id = self._get_uav_id(message["device"])
        uav = self.driver.get_or_create_uav(uav_id)
        uav.update_status(position=message["position"], heading=message["heading"])
        self.app.request_to_send_UAV_INF_message_for([uav_id])

    def _get_uav_id(self, device_id):
        result = self._device_to_uav_id.get(device_id)
        if result is None:
            result = make_valid_uav_id(self._id_format.format(device_id))
            self._device_to_uav_id[device_id] = result
        return result

    async def run(self, app, configuration, logger):
        connection, parser = create_gps_connection(
            connection=configuration.get("connection", "gpsd"),
            format=configuration.get("format", "auto"),
        )

        with ExitStack() as stack:
            stack.enter_context(
                app.connection_registry.use(
                    connection, "GPS", "GPS link", purpose=ConnectionPurpose.gps
                )
            )

            await app.supervise(
                connection, task=partial(self.handle_gps_messages, parser=parser)
            )


construct = GPSExtension
