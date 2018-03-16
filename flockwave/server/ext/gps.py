"""Extension that can connect to an external GPS receiver and show the
location data from the GPS as a beacon.
"""

from contextlib import closing
from eventlet import spawn

from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.errors import NotSupportedError


log = None
thread = None


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
        (Connection, str): an appropriately configured connection object,
            and a string describing the format of the data that will arrive
            from the connection
    """
    if format is None:
        format = "auto"

    if connection == "gpsd":
        if format == "auto":
            format = "gpsd"
        connection = "tcp://localhost:2947"

    if ":" not in connection:
        connection = "serial:{0}".format(connection)

    if format != "gpsd":
        raise NotSupportedError("only gpsd is suported at the moment")

    return create_connection(connection), format


def handle_gps_messages(connection):
    connection.open()
    with closing(connection):
        while True:
            connection.wait_until_connected()
            data = connection.read(blocking=True)
            # TODO(ntamas): process the data here


def load(app, configuration, logger):
    """Loads the extension."""
    global log, thread

    connection, format = create_gps_connection(
        connection=configuration.get("connection", "gpsd"),
        format=configuration.get("format", "auto")
    )
    connection = reconnecting(connection)
    log = logger

    app.connection_registry.add(connection, "gps", "GPS link")

    thread = spawn(handle_gps_messages, connection)


def unload(app, configuration):
    global log, thread

    if thread:
        thread.cancel()
        thread = None

    app.connection_registry.remove("gps")
    log = None
