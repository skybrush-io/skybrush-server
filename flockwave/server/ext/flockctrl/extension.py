"""Flockwave server extension that adds support for drone flocks using the
``flockctrl`` protocol.
"""

from blinker import Signal
from eventlet import spawn_n
from xbee import ZigBee

from flockwave.server.connections import create_connection, reconnecting
from flockwave.server.ext.base import UAVExtensionBase
from flockwave.server.model import ConnectionPurpose

from .driver import FlockCtrlDriver
from .errors import ParseError
from .parser import FlockCtrlParser

__all__ = ("construct", )


class FlockCtrlDronesExtension(UAVExtensionBase):
    """Extension that adds support for drone flocks using the ``flockctrl``
    protocol.
    """

    def __init__(self):
        super(FlockCtrlDronesExtension, self).__init__()
        self._driver = None
        self._flockctrl_parser = FlockCtrlParser()
        self._xbee_lowlevel = None
        self._xbee_thread = None

    def _create_driver(self):
        return FlockCtrlDriver()

    def configure(self, configuration):
        self.xbee_lowlevel = self._configure_lowlevel_xbee_connection(
            configuration.get("connection"))
        super(FlockCtrlDronesExtension, self).configure(configuration)

    def unload(self):
        self.xbee_lowlevel = None

    @property
    def xbee_lowlevel(self):
        return self._xbee_lowlevel

    @xbee_lowlevel.setter
    def xbee_lowlevel(self, value):
        if self._xbee_lowlevel is not None:
            self._xbee_thread.kill()
            self._xbee_lowlevel.close()
            self.app.connection_registry.remove("XBee")

        self._xbee_lowlevel = value

        if self._xbee_lowlevel is not None:
            self.app.connection_registry.add(
                self._xbee_lowlevel, "XBee",
                description="Upstream XBee connection for FlockCtrl drones",
                purpose=ConnectionPurpose.uavRadioLink
            )
            self._xbee_lowlevel.open()

            thread = XBeeThread(self, self._xbee_lowlevel)
            thread.on_frame.connect(self._handle_inbound_xbee_frame)
            self._xbee_thread = spawn_n(thread.run)

    def _configure_lowlevel_xbee_connection(self, specifier):
        """Configures the low-level XBee connection object from the given
        connection specifier parsed from the extension configuration.

        Parameters:
            specifier (str): the connection specifier URL that tells the
                extension how to find the serial port to which the XBee
                is connected

        Returns:
            Connection: an abstract connection that can be used to read and
                write byte-level stuff from/to the XBee
        """
        return reconnecting(create_connection(specifier))

    def configure_driver(self, driver, configuration):
        """Configures the driver that will manage the UAVs created by
        this extension.

        It is assumed that the driver is already set up in ``self.driver``
        when this function is called, and it is already associated to the
        server application.

        Parameters:
            driver (UAVDriver): the driver to configure
            configuration (dict): the configuration dictionary of the
                extension
        """
        driver.id_format = configuration.get("id_format", "{0:02}")
        driver.log = self.log.getChild("driver")

    def _handle_inbound_xbee_frame(self, sender, frame):
        """Handles an inbound XBee data frame."""
        # We are interested in real received packets only
        data = frame.get("rf_data")
        if frame.get("id") != "rx" or not data:
            return

        try:
            packet = self._flockctrl_parser.parse(data)
        except ParseError as ex:
            self.log.warn("Failed to parse FlockCtrl packet of length "
                          "{0}: {1!r}".format(len(data), data[:32]))
            self.log.exception(ex)
            return

        self._driver.handle_inbound_packet(packet)


class XBeeThread(object):
    """Green thread that reads incoming packets from an XBee serial
    connection and dispatches signals for every one of them.

    The thread is running within the application context of the Flockwave
    server application.
    """

    on_frame = Signal()

    def __init__(self, ext, connection):
        """Constructor.

        Parameters:
            ext (FlockctrlDronesExtension): the extension that hosts this
                thread
            connection (Connection): the connection to the serial port where
                the XBee can be found.
        """
        self.ext = ext
        self._connection = connection
        self._xbee = ZigBee(connection)

    def _callback(self, frame):
        """Callback function called for every single frame read from the
        XBee.
        """
        self.on_frame.send(self, frame=frame)

    def _error_callback(self, exception):
        """Callback function called when an exception happens while waiting
        for a data frame.
        """
        self.ext.log.exception(exception)

    def run(self):
        """Waits for incoming frames on the associated low-level XBee
        connection and dispatches a signal for every one of them.

        The body of this function is mostly copied from `XBeeBase.run()`.
        Sadly enough, I haven't found a way to prevent XBeeBase_ from
        spawning a new thread on its own when passing a callback to it in
        the constructor.
        """
        with self.ext.app.app_context():
            while True:
                try:
                    self._callback(self._xbee.wait_read_frame())
                except Exception as ex:
                    self._error_callback(ex)


construct = FlockCtrlDronesExtension
