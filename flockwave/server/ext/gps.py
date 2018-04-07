"""Extension that can connect to an external GPS receiver and show the
location data from the GPS as a beacon.
"""

from contextlib import closing
from eventlet import spawn

from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.encoders import JSONEncoder
from flockwave.server.errors import NotSupportedError
from flockwave.server.model.uav import PassiveUAVDriver
from flockwave.server.parsers import LineParser

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
        connection = "serial:{0}".format(connection)

    if format == "gpsd":
        parser = LineParser(decoder=parse_incoming_gpsd_message, min_length=1)
    else:
        raise NotSupportedError(
            "{0!r} format is suported at the moment".format(format)
        )

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

    if cls == "TPV":
        lat, lon = data.get("lat"), data.get("lon")
        if lat is not None and lon is not None:
            result.update(
                device=data.get("device", "gpsd"),
                position=[lat, lon, data.get("alt", 0)],
                heading=data.get("track", 0)
            )

    return result


def handle_gps_messages(connection, parser):
    """Worker green thread that reads incoming messages from the given
    connection, parses them using the given parser and then processes them
    to update the status of the beacons managed by this extension.
    """
    connection.open()
    with closing(connection):
        while True:
            connection.wait_until_connected()
            while True:
                data, addr = connection.read(blocking=True)
                if not data:
                    break

                for message in parser.feed(data):
                    pass       # TODO(ntamas)


class GPSExtension(UAVExtensionBase):
    """Extension that tracks position information received from external GPS
    devices and creates UAVs in the UAV registry corresponding to the GPS
    devices.
    """

    def __init__(self):
        """Constructor."""
        super(GPSExtension, self).__init__()
        self._thread = None

    def _create_driver(self):
        return PassiveUAVDriver()

    def configure(self, configuration):
        """Loads the extension."""
        connection, parser = create_gps_connection(
            connection=configuration.get("connection", "gpsd"),
            format=configuration.get("format", "auto")
        )
        connection = reconnecting(connection)

        self.app.connection_registry.add(connection, "gps", "GPS link")

        self._thread = spawn(handle_gps_messages, connection, parser)

    def teardown(self):
        if self._thread:
            self._thread.cancel()
            self._thread = None

        self.app.connection_registry.remove("gps")


construct = GPSExtension
