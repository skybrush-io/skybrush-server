"""Connection for a serial port."""

from __future__ import absolute_import, print_function

import csv
import gzip

from .base import ConnectionBase, ConnectionState
from .factory import create_connection
from serial import Serial, STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, \
    STOPBITS_TWO
from time import time

__all__ = ("SerialPortConnection", )

# TODO: recording and replaying should be made more generic


@create_connection.register("serial")
class SerialPortConnection(ConnectionBase):
    """Connection for a serial port.

    This object is a wrapper around a PySerial serial port object. It provides
    an interface that should mostly be compatible with the wrapped serial
    port object, although not all methods are forwarded. Currently we forward
    the following methods to the underlying serial port:

        - ``inWaiting()``

        - ``read()``

        - ``write()``
    """

    def __init__(self, path, baud, stopbits=1):
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
        self._serial = None

    def close(self):
        """Closes the serial port connection."""
        if self.state == ConnectionState.DISCONNECTED:
            return
        self._serial.close()
        self._serial = None
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
            raise ValueError("unsupported stop bit count: {0!r}"
                             .format(self._stopbits))

        self._serial = Serial(self._path, self._baud, stopbits=stopbits)
        self._set_state(ConnectionState.CONNECTED)

    def inWaiting(self):
        """Returns the number of bytes waiting to be read from the serial
        port.

        The name of this function is camel-cased to make it API-compatible
        with ``pyserial``.
        """
        if self._serial is not None:
            try:
                result = self._serial.inWaiting()
            except IOError as ex:
                self._handle_error(ex)
                result = 0
        else:
            result = 0
        return result

    @property
    def fd(self):
        """Returns the file-like object behind the connection."""
        return self._serial

    def fileno(self):
        """Returns the file handle behind the connection."""
        return self._serial.fileno()

    def flush(self):
        """Flushes the data recently written to the connection."""
        self._serial.flush()

    def read(self, size=1):
        """Reads the given number of bytes from the connection.

        Parameters:
            size (int): the number of bytes to read

        Returns:
            bytes: the data that was read
        """
        if self._serial is not None:
            try:
                result = self._serial.read(size)
            except IOError as ex:
                self._handle_error(ex)
                result = b""
        else:
            result = b""
        return result

    def write(self, data):
        """Writes the given data to the serial connection.

        Parameters:
            data (bytes): the data to write
        """
        if self._serial is not None:
            try:
                return self._serial.write(data)
            except IOError as ex:
                self._handle_error(ex)
                return 0
        else:
            return 0


class RecordableSerialPortConnection(SerialPortConnection):
    """Serial port connection that records all reads and writes into a log
    file that can be used later to replay the communication sequence on
    the port.
    """

    def __init__(self, *args, **kwds):
        """Constructor.

        All constructor arguments are forwarded to the superclass.
        """
        super(RecordableSerialPortConnection, self).__init__(*args, **kwds)
        self._log_stream = None
        self._log_header_written = False
        self._recording = False

    @property
    def log_stream(self):
        """The log stream that records the communication. It should be a file
        opened in binary mode.
        """
        return self._log_stream

    @log_stream.setter
    def log_stream(self, value):
        if value == self._log_stream:
            return

        if self.recording_and_has_stream:
            self._add_entry_to_log("stop")

        self._log_stream = value
        self._log_header_written = False

        if self.recording_and_has_stream:
            self._add_entry_to_log("start")

    @property
    def recording(self):
        """Whether we are currently recording the bytes written or read
        into the log stream (if we have a log stream).
        """
        return self._recording

    @property
    def recording_and_has_stream(self):
        """Whether we are currently recording the bytes written or read
        into the log stream *and* we also have a log stream.
        """
        return self._recording and self._log_stream is not None

    @recording.setter
    def recording(self, value):
        value = bool(value)
        if value == self._recording:
            return

        if self.recording_and_has_stream:
            self._add_entry_to_log("stop")

        self._recording = value

        if self.recording_and_has_stream:
            self._add_entry_to_log("start")

    def read(self, size=1):
        """Reads the given number of bytes from the connection.

        Parameters:
            size (int): the number of bytes to read

        Returns:
            bytes: the data that was read
        """
        result = super(RecordableSerialPortConnection, self).read(size)
        if self.recording_and_has_stream and result:
            self._add_entry_to_log("read", result)
        return result

    def write(self, data):
        """Writes the given data to the serial connection.

        Parameters:
            data (bytes): the data to write
        """
        result = super(RecordableSerialPortConnection, self).write(data)
        if self.recording_and_has_stream and result:
            self._add_entry_to_log("write", data)
        return result

    def _add_entry_to_log(self, entry_type, data=None):
        if not self._log_header_written:
            self._log_stream.write(b"# format: tsv\r\n")
            self._log_header_written = True

        now = time()
        row = [entry_type.encode("iso-8859-1"), str(now).encode("iso-8859-1")]
        if isinstance(data, bytes):
            row.append(data.encode("string-escape"))

        self._log_stream.write(b"\t".join(row))
        self._log_stream.write(b"\r\n")


@create_connection.register("serial+replay")
class ReplayedSerialPortConnection(ConnectionBase):
    """Fake serial port connection object that replays a previously recorded
    serial port session in real time. The recording that the connection uses
    must have been created by a RecordableSerialPortConnection_ object.
    """

    def __init__(self, path=None, autoclose=False):
        super(ReplayedSerialPortConnection, self).__init__()
        self._log_reader = None
        self._log_stream = None
        self._next_entry = None
        self._buffer = []
        self.path = path
        self._timedelta = None
        self.autoclose = bool(autoclose)

    @property
    def log_stream(self):
        """The log stream that contains the recorded communication. It should
        be a file opened in binary mode. When it is set to ``None``, the
        filename specified by the ``path`` attribute of the class will be
        opened when the connection is opened.
        """
        return self._log_stream

    @log_stream.setter
    def log_stream(self, value):
        if value == self._log_stream:
            return

        old_state = self.state
        if old_state == ConnectionState.CONNECTED:
            self.close()

        self._log_stream = value
        log_lines = (line for line in self._log_stream
                     if line and not line.startswith(b"#"))
        self._log_reader = csv.reader(log_lines, dialect="excel-tab",
                                      quoting=csv.QUOTE_NONE)
        self._buffer = []

        if old_state == ConnectionState.CONNECTED:
            self.open()

    def close(self):
        if self.state == ConnectionState.DISCONNECTED:
            return

        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None
            self._log_reader = None

        self._timedelta = None
        self._set_state(ConnectionState.DISCONNECTED)

    def open(self):
        if self.state == ConnectionState.CONNECTED:
            return

        if self.log_stream is None and self.path is not None:
            self.log_stream = gzip.open(self.path, "rb")

        if self._log_reader is None:
            if self.swallow_exceptions:
                return
            else:
                raise ValueError("log stream must be set before the "
                                 "connection is opened")

        self._next_entry = self._read_next_log_entry("read")
        if self._next_entry is not None:
            now = time()
            self._timedelta = now - self._next_entry[1]

        self._set_state(ConnectionState.CONNECTED)

    def inWaiting(self):
        if self._timedelta is None:
            return 0
        else:
            self._fill_buffer_up_to(time())
            return len(self._buffer)

    def read(self, size=1):
        if size == 0:
            return b""

        # This call must ensure that we never return zero bytes if size > 0
        while True:
            self._fill_buffer_up_to(time())
            if self._buffer:
                to_return = self._buffer[:size]
                del self._buffer[:size]
                return b"".join(to_return)

    def write(self, data):
        # Writing is a no-op
        pass

    def _fill_buffer_up_to(self, timestamp):
        """Fills the buffer of the connection with bytes that were recorded
        up to the given timestamp. The timestamp is given according to the
        clock of the machine that *replays* the recording; ``self._timedelta``
        will be used to transform the timestamp back to the clock of the
        machine that *recorded* the data. The timestamp is *inclusive*.
        """
        if self._timedelta is None:
            return

        timestamp -= self._timedelta
        while True:
            entry = self._read_next_log_entry(b"read")
            if entry is None:
                break
            if entry[1] > timestamp:
                self._next_entry = entry
                break
            self._buffer.extend(entry[2])

    def _read_next_log_entry(self, entry_type=None):
        """Reads the next log entry from the log stream.

        Parameters:
            entry_type (Optional[bytes]): when specified, skips entries that
                do not match the given type

        Returns:
            Optional[bytes,float,bytes]: a tuple containing the entry type,
                the timestamp and the data corresponding to the entry, or
                ``None`` if there are no more entries
        """
        if self._next_entry is not None:
            result = self._next_entry
            self._next_entry = None
            return result
        else:
            while True:
                entry = next(self._log_reader, None)
                if entry is not None:
                    if entry_type is not None and entry[0] != entry_type:
                        continue
                    entry = entry[0], float(entry[1]), \
                        entry[2].decode("string-escape")
                elif self.autoclose:
                    self.close()
                return entry


def test_recordable_serial_port():
    import sys
    from cStringIO import StringIO
    from groundctrl.util import tee
    from os import read, ttyname, write
    from pty import openpty
    from time import sleep

    # Create a fake serial port
    master, slave = openpty()
    slave_name = ttyname(slave)

    # Create a serial port connection for the fake port
    slave_port = RecordableSerialPortConnection(slave_name, 115200)
    log = StringIO()
    slave_port.log_stream = tee(log, sys.stdout)
    slave_port.open()

    # Start writing stuff to the fake port
    slave_port.write(b"test message that is not recorded")
    slave_port.recording = True
    for i in range(5):
        slave_port.write(b"test message {0}".format(i))
        sleep(0.2)
    slave_port.write(b"final message\nbroken into\nmultiple lines\n"
                     b"with \\ backslashes!")

    # Read it back from the master and then send something
    read(master, 1000)
    for i in range(5):
        message = b"test message {0}".format(i)
        write(master, message)

        num_read = 0
        while num_read < len(message):
            num_read += len(slave_port.read(slave_port.inWaiting()))

        sleep(0.2)

    message = b"final message\nbroken into\nmultiple lines\n"\
              b"with \\ backslashes!"
    write(master, message)
    num_read = 0
    while num_read < len(message):
        num_read += len(slave_port.read(slave_port.inWaiting()))

    # Stop recording
    slave_port.recording = False

    # Replay the recording
    conn = ReplayedSerialPortConnection(StringIO(log.getvalue()))
    conn.open()
    while True:
        sys.stdout.write(conn.read(conn.inWaiting()))
        sys.stdout.flush()
