"""Base connection classes."""

from abc import ABCMeta, abstractmethod, abstractproperty
from blinker import Signal
from enum import Enum
from eventlet.green.threading import RLock
from six import with_metaclass

__all__ = ("Connection", "ConnectionState", "ConnectionBase")


ConnectionState = Enum("ConnectionState",
                       "DISCONNECTED CONNECTING CONNECTED DISCONNECTING")


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
                self.connected.send(self)
            if new_state == ConnectionState.DISCONNECTED and \
                    self._is_connected:
                self._is_connected = False
                self.disconnected.send(self)

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

    def _handle_error(self):
        """Handles exceptions that have happened during reads and writes."""
        if self._swallow_exceptions:
            # Just close the connection
            self.close()
        else:
            # Let the user handle the exception
            raise
