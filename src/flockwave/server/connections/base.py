"""Base connection classes."""

import logging
import os

from abc import ABCMeta, abstractmethod, abstractproperty
from blinker import Signal
from enum import Enum
from trio import wrap_file
from trio_util import AsyncBool


__all__ = (
    "Connection",
    "ConnectionState",
    "ConnectionBase",
    "FDConnectionBase",
    "ConnectionWrapperBase",
)


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"

    @property
    def is_transitioning(self):
        return self in (ConnectionState.CONNECTING, ConnectionState.DISCONNECTING)


log = logging.getLogger(__name__.rpartition(".")[0])


class Connection(metaclass=ABCMeta):
    """Interface specification for stateful connection objects."""

    connected = Signal(doc="Signal sent after the connection was established.")
    disconnected = Signal(doc="Signal sent after the connection was torn down.")
    state_changed = Signal(
        doc="""\
        Signal sent whenever the state of the connection changes.

        Parameters:
            new_state (str): the new state
            old_state (str): the old state
        """
    )

    @abstractmethod
    async def open(self):
        """Opens the connection. No-op if the connection is open already."""
        raise NotImplementedError

    @abstractmethod
    async def close(self):
        """Closes the connection. No-op if the connection is closed already."""
        raise NotImplementedError

    @property
    def is_disconnected(self):
        """Returns whether the connection is disconnected (and not connecting and
        not disconnecting)."""
        return self.state is ConnectionState.DISCONNECTED

    @property
    def is_connected(self):
        """Returns whether the connection is connected."""
        return self.state is ConnectionState.CONNECTED

    @property
    def is_transitioning(self):
        """Returns whether connection is currently transitioning."""
        return self.state.is_transitioning

    @abstractproperty
    def state(self):
        """Returns the state of the connection; one of the constants from
        the ``ConnectionState`` enum.
        """
        raise NotImplementedError

    @abstractmethod
    async def wait_until_connected(self):
        """Blocks the current green thread until the connection becomes
        connected. Returns immediately if the connection is already
        connected.
        """
        raise NotImplementedError

    @abstractmethod
    async def wait_until_disconnected(self):
        """Blocks the execution until the connection becomes disconnected.
        Returns immediately if the connection is already disconnected.
        """
        raise NotImplementedError

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()


class ConnectionBase(Connection):
    """Base class for stateful connection objects.

    Connection objects may be in one of the following four states:

        - ``DISCONNECTED``: the connection is down

        - ``CONNECTING``: the connection is being established

        - ``CONNECTED``: the connection is up

        - ``DISCONNECTING``: the connection is being closed

    Each connection object provides three signals that interested parties
    may connect to if they want to be notified about changes in the connection
    states: ``state_changed``, ``connected`` and ``disconnected``.
    ``state_changed`` is fired whenever the connection state changes.
    ``connected`` is fired when the connection enters the ``CONNECTED`` state
    from any other state. ``disconnected`` is fired when the connection enters
    the ``DISCONNECTED`` state from any other state.

    Classes derived from this base class *MUST NOT* set the ``_state`` variable
    directly; they *MUST* use the ``_set_state`` method instead to ensure that
    the signals are dispatched appropriately.
    """

    def __init__(self):
        """Constructor."""
        self._state = ConnectionState.DISCONNECTED

        self._is_connected = AsyncBool(False)
        self._is_disconnected = AsyncBool(True)

    @property
    def state(self):
        """The state of the connection."""
        return self._state

    def _set_state(self, new_state):
        """Sets the state of the connection to a new value and sends the
        appropriate signals.
        """
        old_state = self._state
        if new_state == old_state:
            return

        self._state = new_state

        self.state_changed.send(self, old_state=old_state, new_state=new_state)

        if not self._is_connected.value and new_state is ConnectionState.CONNECTED:
            self._is_connected.value = True
            self.connected.send(self)

        if (
            not self._is_disconnected.value
            and new_state is ConnectionState.DISCONNECTED
        ):
            self._is_disconnected.value = True
            self.disconnected.send(self)

        if self._is_connected.value and new_state is not ConnectionState.CONNECTED:
            self._is_connected.value = False

        if (
            self._is_disconnected.value
            and new_state is not ConnectionState.DISCONNECTED
        ):
            self._is_disconnected.value = False

    async def close(self):
        """Base implementation of Connection.close() that manages the state of
        the connection correctly.

        Typically, you don't need to override this method in subclasses;
        override `_close()` instead.
        """
        if self.state is ConnectionState.DISCONNECTED:
            return
        elif self.state is ConnectionState.DISCONNECTING:
            return await self.wait_until_disconnected()
        elif self.state is ConnectionState.CONNECTING:
            await self.wait_until_connected()

        self._set_state(ConnectionState.DISCONNECTING)
        success = False
        try:
            # TODO(ntamas): use a timeout here!
            await self._close()
            success = True
        finally:
            self._set_state(
                ConnectionState.DISCONNECTED if success else ConnectionState.CONNECTED
            )

    async def open(self):
        """Base implementation of Connection.open() that manages the state
        of the connection correctly.

        Typically, you don't need to override this method in subclasses;
        override `_open()` instead.
        """
        if self.state is ConnectionState.CONNECTED:
            return
        elif self.state is ConnectionState.CONNECTING:
            return await self.wait_until_connected()
        elif self.state is ConnectionState.DISCONNECTING:
            await self.wait_until_disconnected()

        self._set_state(ConnectionState.CONNECTING)
        success = False
        try:
            # TODO(ntamas): use a timeout here!
            await self._open()
            success = True
        finally:
            self._set_state(
                ConnectionState.CONNECTED if success else ConnectionState.DISCONNECTED
            )

    async def wait_until_connected(self):
        """Blocks the execution until the connection becomes connected."""
        await self._is_connected.wait_value(True)

    async def wait_until_disconnected(self):
        """Blocks the execution until the connection becomes disconnected."""
        await self._is_disconnected.wait_value(True)

    @abstractmethod
    async def _open(self):
        """Internal implementation of `ConnectionBase.open()`.

        Override this method in subclasses to implement how your connection
        is opened. No need to update the state variable from inside this
        method; the caller will do it automatically.
        """
        raise NotImplementedError

    @abstractmethod
    async def _close(self):
        """Internal implementation of `ConnectionBase.close()`.

        Override this method in subclasses to implement how your connection
        is closed. No need to update the state variable from inside this
        method; the caller will do it automatically.
        """
        raise NotImplementedError


class FDConnectionBase(ConnectionBase):
    """Base class for connection objects that have an underlying numeric
    file handle or file-like object.
    """

    file_handle_changed = Signal(
        doc="""\
        Signal sent whenever the file handle associated to the connection
        changes.

        Parameters:
            new_handle (int): the new file handle
            old_handle (int): the old file handle
        """
    )

    def __init__(self):
        """Constructor."""
        super(FDConnectionBase, self).__init__()
        self._file_handle = None
        self._file_object = None

    def fileno(self):
        """Returns the underlying file handle of the connection, for sake of
        compatibility with other file-like objects in Python.
        """
        return self._file_handle

    async def flush(self):
        """Flushes the data recently written to the connection."""
        if self._file_object is not None:
            await self._file_object.flush()

    @property
    def fd(self):
        """Returns the underlying file handle of the connection."""
        return self._file_handle

    @property
    def fp(self):
        """Returns the underlying file-like object of the connection."""
        return self._file_object

    @abstractmethod
    async def read(self, size=-1):
        """Reads the given number of bytes from the connection.

        Parameters:
            size (int): the number of bytes to read; -1 to read all
                available data.

        Returns:
            bytes: the data that was read
        """
        raise NotImplementedError

    @abstractmethod
    async def write(self, data):
        """Writes the given data to the connection.

        Parameters:
            data (bytes): the data to write

        Returns:
            int: the number of bytes written; -1 if the connection is not
                open yet
        """
        raise NotImplementedError

    def _attach(self, handle_or_object):
        """Associates a file handle or file-like object to the connection.
        This is the method that derived classes should use whenever the
        connection is associated to a new file handle or file-like object.
        """
        if handle_or_object is None:
            handle, obj = None, None
        elif isinstance(handle_or_object, int):
            handle, obj = handle_or_object, os.fdopen(handle_or_object)
        else:
            handle, obj = handle_or_object.fileno(), handle_or_object

        # Wrap the raw sync file handle in Trio's async file handle
        obj = wrap_file(obj)

        old_handle = self._file_handle
        self._set_file_handle(handle)
        self._set_file_object(obj)

        if old_handle != self._file_handle:
            self.file_handle_changed.send(
                self, old_handle=old_handle, new_handle=self._file_handle
            )

    def _detach(self):
        """Detaches the connection from its current associated file handle
        or file-like object.
        """
        self._attach(None)

    def _set_file_handle(self, value):
        """Setter for the ``_file_handle`` property. Derived classes should
        not set ``_file_handle`` or ``_file_object`` directly; they should
        use ``_attach()`` or ``_detach()`` instead.

        Parameters:
            value (int): the new file handle

        Returns:
            bool: whether the file handle has changed
        """
        if self._file_handle == value:
            return False

        self._file_handle = value
        return True

    def _set_file_object(self, value):
        """Setter for the ``_file_object`` property. Derived classes should
        not set ``_file_handle`` or ``_file_object`` directly; they should
        use ``_attach()`` or ``_detach()`` instead.

        Parameters:
            value (Optional[file]): the new file object or ``None`` if the
                connection is not associated to a file-like object (which
                may happen even if there is a file handle if the file handle
                does not have a file-like object representation)

        Returns:
            bool: whether the file object has changed
        """
        if self._file_object == value:
            return False

        self._file_object = value
        return True


# TOD(ntamas): this turned out to be a bad idea, let's get rid of it if
# possible
class ConnectionWrapperBase(ConnectionBase):
    """Base class for connection objects that wrap other connections."""

    def __init__(self, wrapped=None):
        """Constructor.

        Parameters:
            wrapped (Connection): the wrapped connection
        """
        super(ConnectionWrapperBase, self).__init__()
        self._wrapped = None
        self._set_wrapped(wrapped)

    def _redispatch_signal(self, sender, *args, **kwds):
        """Handler that redispatches signals from the wrapped connection."""
        if hasattr(self, "file_handle_changed"):
            self.file_handle_changed.send(self, *args, **kwds)

    def _set_wrapped(self, value):
        """Function that must be used by derived classes to change the
        connection that the wrapper wraps.

        Parameters:
            value (ConnectionBase): the new connection that the wrapper will
                wrap

        Returns:
            ConnectionBase: the old connection that the wrapped used to wrap
        """
        if self._wrapped == value:
            return

        old_value = self._wrapped
        self._wrapped = value

        self._wrapped_connection_changed(old_value, value)

    def _wrapped_connection_changed(self, old_conn, new_conn):
        """Hook function that is called when the connection wrapped by this
        connection changes.

        Parameters:
            old_conn (ConnectionBase): the old connection that is not wrapped
                by this wrapper any more
            new_conn (ConnectionBase): the new connection that is now wrapped
                by this wrapper
        """
        if hasattr(old_conn, "file_handle_changed"):
            old_conn.file_handle_changed.disconnect(
                self._redispatch_signal, sender=old_conn
            )
        if hasattr(new_conn, "file_handle_changed"):
            new_conn.file_handle_changed.connect(
                self._redispatch_signal, sender=new_conn
            )

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __hasattr__(self, name):
        return hasattr(self._wrapped, name)
