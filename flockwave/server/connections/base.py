"""Base connection classes."""

import logging
import os

from abc import ABCMeta, abstractmethod, abstractproperty
from blinker import Signal
from enum import Enum
from eventlet.event import Event
from eventlet.green.threading import RLock
from future.utils import with_metaclass
from select import select

__all__ = ("Connection", "ConnectionState", "ConnectionBase",
           "FDConnectionBase", "ConnectionWrapperBase")


ConnectionState = Enum("ConnectionState",
                       "DISCONNECTED CONNECTING CONNECTED DISCONNECTING")

log = logging.getLogger(__name__.rpartition(".")[0])


class Connection(with_metaclass(ABCMeta, object)):
    """Interface specification for stateful connection objects."""

    connected = Signal(doc="Signal sent after the connection was established.")
    disconnected = Signal(
        doc="Signal sent after the connection was torn down.")
    state_changed = Signal(
        doc="""\
        Signal sent whenever the state of the connection changes.

        Parameters:
            new_state (str): the new state
            old_state (str): the old state
        """
    )

    @abstractmethod
    def open(self):
        """Opens the connection. No-op if the connection is open already."""
        raise NotImplementedError

    @abstractmethod
    def close(self):
        """Closes the connection. No-op if the connection is closed already."""
        raise NotImplementedError

    @property
    def is_connected(self):
        """Returns whether the connection is connected."""
        return self.state is ConnectionState.CONNECTED

    @property
    def is_transitioning(self):
        """Returns whether connection is currently transitioning."""
        return self.state in (ConnectionState.CONNECTING,
                              ConnectionState.DISCONNECTING)

    @abstractproperty
    def state(self):
        """Returns the state of the connection; one of the constants from
        the ``ConnectionState`` enum.
        """
        raise NotImplementedError

    def wait_until_connected(self):
        """Blocks the current green thread until the connection becomes
        connected. Returns immediately if the connection is already
        connected.
        """
        raise NotImplementedError


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

    The ``state`` property of the connection is thread-safe.

    Classes derived from this base class *MUST NOT* set the ``_state`` variable
    directly; they *MUST* use the ``_set_state`` method instead to ensure that
    the signals are dispatched appropriately.
    """

    def __init__(self):
        """Constructor."""
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = RLock()
        self._is_connected = False
        self._is_connected_event = None
        self._is_not_connected_event = None
        self._swallow_exceptions = False

    @property
    def state(self):
        """The state of the connection."""
        return self._state

    def _set_state(self, new_state):
        """Sets the state of the connection to a new value and sends the
        appropriate signals.
        """
        # Locking is actually not needed here because we are using green
        # threads using Eventlet, but this way we can make use of this
        # class in threaded environments as well.
        with self._state_lock:
            old_state = self._state
            if new_state == old_state:
                return

            self._state = new_state

            self.state_changed.send(self, old_state=old_state,
                                    new_state=new_state)

            if new_state == ConnectionState.CONNECTED and \
                    not self._is_connected:
                self._is_connected = True
                if self._is_connected_event:
                    self._is_connected_event.send(True)
                    self._is_connected_event = None
                self.connected.send(self)

            if new_state == ConnectionState.DISCONNECTED and \
                    self._is_connected:
                self.disconnected.send(self)

            if new_state != ConnectionState.CONNECTED and \
                    self._is_connected:
                self._is_connected = False
                if self._is_not_connected_event:
                    self._is_not_connected_event.send(True)
                    self._is_not_connected_event = None

    @property
    def swallow_exceptions(self):
        """Whether the connection should swallow read/write and connection
        errors and respond to them simply by closing the connection instead.
        Useful when the connection is wrapped in a ReconnectionWrapper_.
        """
        return self._swallow_exceptions

    @swallow_exceptions.setter
    def swallow_exceptions(self, value):
        self._swallow_exceptions = bool(value)

    def wait_until_connected(self):
        """Blocks the current green thread until the connection becomes
        connected. Returns immediately if the connection is already
        connected.
        """
        if self.is_connected:
            return

        if self._is_connected_event is None:
            self._is_connected_event = Event()

        self._is_connected_event.wait()

    def wait_until_not_connected(self):
        """Blocks the current green thread until the connection becomes
        *not* connected (where this means being in any state other than
        CONNECTED). Returns immediately if the connection is not connected.
        """
        if not self.is_connected:
            return

        if self._is_not_connected_event is None:
            self._is_not_connected_event = Event()

        self._is_not_connected_event.wait()

    def _handle_error(self, exception=None):
        """Handles exceptions that have happened during reads and writes.

        Parameters:
            exception (Optional[Exception]): the exception that was raised
                during a read or write
        """
        if self._swallow_exceptions:
            # Log the exception if we have one
            if exception is not None:
                log.exception(exception)
            # Then close the connection
            self.close()
        else:
            # Let the user handle the exception
            raise


class FDConnectionBase(ConnectionBase):
    """Base class for connection objects that have an underlying numeric file
    handle.
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

    def flush(self):
        """Flushes the data recently written to the connection."""
        if self._file_object is not None:
            self._file_object.flush()
        if self._file_handle is not None:
            os.fsync(self._file_handle)

    @property
    def fd(self):
        """Returns the underlying file handle of the connection."""
        return self._file_handle

    @property
    def fp(self):
        """Returns the underlying file-like object of the connection."""
        return self._file_object

    @abstractmethod
    def read(self, size=-1):
        """Reads the given number of bytes from the connection.

        Parameters:
            size (int): the number of bytes to read; -1 to read all
                available data.

        Returns:
            bytes: the data that was read
        """
        raise NotImplementedError

    @abstractmethod
    def write(self, data):
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
            handle, obj = handle_or_object, None
        else:
            handle, obj = handle_or_object.fileno(), handle_or_object

        old_handle = self._file_handle
        self._set_file_handle(handle)
        self._set_file_object(obj)

        if old_handle != self._file_handle:
            self.file_handle_changed.send(self, old_handle=old_handle,
                                          new_handle=self._file_handle)

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

    def wait_until_readable(self, timeout=None):
        """Blocks the current thread until the file descriptor becomes
        readable.

        Parameters:
            timeout (Optional[float]): maximum number of seconds to wait for
                the file descriptor
        """
        while True:
            # TODO(ntamas): this is not correct; timeout should be adjusted
            # if the select() call returns with an empty list
            if timeout is not None:
                rlist, _, _ = select([self], [], [], timeout)
            else:
                rlist, _, _ = select([self], [], [])
            if rlist:
                break

    def wait_until_writable(self, timeout=None):
        """Blocks the current thread until the file descriptor becomes
        writable.

        Parameters:
            timeout (Optional[float]): maximum number of seconds to wait for
                the file descriptor
        """
        while True:
            # TODO(ntamas): this is not correct; timeout should be adjusted
            # if the select() call returns with an empty list
            if timeout is not None:
                _, wlist, _ = select([], [self], [], timeout)
            else:
                _, wlist, _ = select([], [self], [])
            if wlist:
                break


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
