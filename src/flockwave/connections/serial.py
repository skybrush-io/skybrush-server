"""Connection for a serial port."""

from __future__ import absolute_import, print_function

from serial import Serial, STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO

from .base import FDConnectionBase, ConnectionState
from .factory import create_connection

__all__ = ("SerialPortConnection",)


@create_connection.register("serial")
class SerialPortConnection(FDConnectionBase):
    """Connection for a serial port.

    This object is a wrapper around a PySerial serial port object. It provides
    an interface that should mostly be compatible with the wrapped serial
    port object, although not all methods are forwarded. Currently we forward
    the following methods to the underlying serial port:

        - ``inWaiting()``

        - ``read()``

        - ``write()``
    """

    def __init__(self, path, baud=115200, stopbits=1):
        """Constructor.

        Parameters:
            path (str or int): full path to the serial port to open, or a
                file descriptor for an already opened serial port.
            baud (int): the baud rate to use when opening the port
            stopbits (int or float): the number of stop bits to use. Must be
                1, 1.5 or 2.
        """
        super(SerialPortConnection, self).__init__()
        self._path = path
        self._baud = baud
        self._stopbits = stopbits

    def close(self):
        """Closes the serial port connection."""
        if self.state == ConnectionState.DISCONNECTED:
            return
        self._file_object.close()
        self._detach()
        self._set_state(ConnectionState.DISCONNECTED)

    def open(self):
        """Opens the serial port connection."""
        if self.state == ConnectionState.CONNECTED:
            return

        if self._stopbits == 1:
            stopbits = STOPBITS_ONE
        elif self._stopbits == 1.5:
            stopbits = STOPBITS_ONE_POINT_FIVE
        elif self._stopbits == 2:
            stopbits = STOPBITS_TWO
        else:
            raise ValueError("unsupported stop bit count: {0!r}".format(self._stopbits))

        try:
            serial = Serial(self._path, self._baud, stopbits=stopbits)
            self._attach(serial)
            self._set_state(ConnectionState.CONNECTED)
        except OSError:
            self._handle_error()

    def inWaiting(self):
        """Returns the number of bytes waiting to be read from the serial
        port.

        The name of this function is camel-cased to make it API-compatible
        with ``pyserial``.
        """
        if self._file_object is not None:
            try:
                result = self._file_object.inWaiting()
            except IOError as ex:
                self._handle_error(ex)
                result = 0
        else:
            result = 0
        return result

    def read(self, size=1):
        """Reads the given number of bytes from the connection.

        Parameters:
            size (int): the number of bytes to read
            blocking (bool): whether the data should be read in a blocking
                manner

        Returns:
            bytes: the data that was read
        """
        if self._file_object is not None:
            try:
                return self._file_object.read(size)
            except IOError as ex:
                self._handle_error(ex)
        return b""

    @property
    def readable(self):
        """Returns whether the connection is currently ready for reading.
        The connection is ready for reading if there is at least one
        byte waiting in the serial port queue.
        """
        return self.inWaiting > 0

    def write(self, data):
        """Writes the given data to the serial connection.

        Parameters:
            data (bytes): the data to write

        Returns:
            int: the number of bytes that were written; -1 if the port is
                not open
        """
        if self._file_object is not None:
            try:
                return self._file_object.write(data)
            except IOError as ex:
                self._handle_error(ex)
                return 0
        else:
            return -1
