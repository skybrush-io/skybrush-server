"""File-based connection object."""

from __future__ import absolute_import, print_function

from .base import ConnectionBase, ConnectionState
from .factory import create_connection

__all__ = ("FileConnection", )


@create_connection.register("file")
class FileConnection(ConnectionBase):
    """Connection object that reads its incoming data from a file or
    file-like object.
    """

    def __init__(self, filename, mode="rb", autoflush=False):
        """Constructor.

        Parameters:
            filename (str): name of the file to read the incoming data from
            mode (str): the mode to open the file with
            autoflush (bool): whether to flush the file automatically after
                each write
        """
        super(FileConnection, self).__init__()

        self.autoflush = bool(autoflush)
        self._filename = filename
        self._mode = mode
        self._fp = None

    def close(self):
        """Closes the file connection."""
        if self.state == ConnectionState.DISCONNECTED:
            return

        self._set_state(ConnectionState.DISCONNECTING)
        self._fp.close()
        self._fp = None
        self._set_state(ConnectionState.DISCONNECTED)

    def open(self):
        """Opens the file connection."""
        if self.state in (ConnectionState.CONNECTED,
                          ConnectionState.CONNECTING):
            return

        self._set_state(ConnectionState.CONNECTING)
        self._fp = open(self._filename, self._mode)
        self._set_state(ConnectionState.CONNECTED)

    @property
    def fd(self):
        """Returns the file-like object behind the connection."""
        return self._fp

    def fileno(self):
        """Returns the file handle behind the connection."""
        return self._fp.fileno()

    def flush(self):
        """Flushes the data recently written to the connection."""
        self._fp.flush()

    def read(self, size=-1):
        """Reads the given number of bytes from the connection.

        Parameters:
            size: the number of bytes to read

        Returns:
            bytes: the data that was read
        """
        return self._fp.read(size)

    def write(self, data):
        """Writes the given data to the connection.

        Parameters:
            data (bytes): the data to write
        """
        result = self._fp.write(data)
        if self.autoflush:
            self.flush()
        return result
