"""Connection for a serial port."""

from __future__ import absolute_import, print_function

from os import dup
from serial import Serial, STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO
from trio.abc import Stream
from trio.hazmat import FdStream, wait_readable
from typing import Optional

from .factory import create_connection
from .stream import StreamConnectionBase

__all__ = ("SerialPortConnection",)


class SerialPortStream(Stream):
    """A Trio stream implementation that talks to a serial port using
    PySerial in a separate thread.
    """

    @classmethod
    async def create(cls, *args, **kwds) -> Stream:
        """Constructs a new `pySerial` serial port object, associates it to a
        SerialStream_ and returns the serial stream itself.

        All positional and keyword arguments are forwarded to the constructor
        of the Serial_ object from `pySerial`.

        Returns:
            the constructed serial stream
        """
        return cls(Serial(timeout=0, *args, **kwds))

    def __init__(self, device: Serial):
        """Constructor.

        Do not use this method unless you know what you are doing; use
        `SerialPortStream.create()` instead.

        Parameters:
            device: the `pySerial` serial port object to manage in this stream.
                It must already be open.
        """
        self._device = device
        self._device.nonblocking()
        self._fd_stream = FdStream(dup(self._device.fileno()))

    async def aclose(self):
        """Closes the serial port."""
        await self._fd_stream.aclose()

    async def receive_some(self, max_bytes: Optional[int] = None) -> bytes:
        result = await self._fd_stream.receive_some(max_bytes)
        if result:
            return result

        # Spurious EOF; this happens because POSIX serial port devices
        # may not return -1 with errno = WOULDBLOCK in case of an EOF
        # condition. So we wait for the port to become readable again. If it
        # becomes readable and _still_ returns no bytes, then this is a real
        # EOF.
        await wait_readable(self._fd_stream.fileno())
        return await self._fd_stream.receive_some(max_bytes)

    async def send_all(self, data: bytes) -> None:
        """Sends some data over the serial port.

        Parameters:
            data: the data to send

        Raises:
            BusyResourceError: if another task is working with this stream
            BrokenResourceError: if something has gone wrong and the stream
                is broken
            ClosedResourceError: if you previously closed this stream object, or
                if another task closes this stream object while `send_all()`
                is running.
        """
        await self._fd_stream.send_all(data)

    async def wait_send_all_might_not_block(self) -> None:
        await self._fd_stream.wait_send_all_might_not_block()


@create_connection.register("serial")
class SerialPortConnection(StreamConnectionBase):
    """Connection for a serial port."""

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

    async def _create_stream(self) -> Stream:
        if self._stopbits == 1:
            stopbits = STOPBITS_ONE
        elif self._stopbits == 1.5:
            stopbits = STOPBITS_ONE_POINT_FIVE
        elif self._stopbits == 2:
            stopbits = STOPBITS_TWO
        else:
            raise ValueError("unsupported stop bit count: {0!r}".format(self._stopbits))

        return await SerialPortStream.create(
            self._path, baudrate=self._baud, stopbits=stopbits
        )
