"""Base classes for stateful connection objects."""

from abc import ABCMeta, abstractmethod, abstractproperty
from blinker import Signal
from enum import Enum
from eventlet import spawn
from eventlet.green.threading import RLock
from eventlet.green.Queue import Queue
from eventlet.green.Queue import Empty as EmptyQueue
from six import with_metaclass
from weakref import ref as weakref

__all__ = ("ConnectionState", "Connection", "ConnectionBase",
           "ReconnectionWrapper", "reconnecting")

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


class ReconnectionWrapper(ConnectionBase):
    """Wraps a connection object and attempts to silently reconnect the
    underlying connection if it breaks or cannot be opened.
    """

    def __init__(self, wrapped):
        """Constructor.

        :param wrapped: the wrapped connection
        :type wrapped: groundctrl.connection.base.Connection
        """
        self._wrapped = wrapped
        self._wrapped.swallow_exceptions = True
        self._lock = RLock()
        self._watchdog = None
        self._watchdog_thread = None

    def __del__(self):
        """Destructor. Kills the watchdog when the wrapper is garbage
        collected.
        """
        self._kill_watchdog(wait=False)

    def open(self):
        """Opens the connection. No-op if the connection is open already."""
        if self.state in (ConnectionState.CONNECTED,
                          ConnectionState.CONNECTING):
            return

        if self._watchdog is None:
            self._watchdog = ReconnectionWatchdog(self._wrapped, self._lock)
            self._watchdog.recovery_state_changed.connect(
                self._watchdog_recovery_state_changed,
                sender=self._watchdog
            )

        self._set_state(ConnectionState.CONNECTING
                        if self._wrapped.state is ConnectionState.DISCONNECTED
                        else ConnectionState.CONNECTED)

        self._watchdog_thread = spawn(self._watchdog.run)

    def close(self):
        """Closes the connection. No-op if the connection is closed already."""
        if self.state in (ConnectionState.DISCONNECTED,
                          ConnectionState.DISCONNECTING):
            return

        self._set_state(ConnectionState.DISCONNECTING)
        self._kill_watchdog(wait=True)
        self._set_state(ConnectionState.DISCONNECTED)

    def _kill_watchdog(self, wait=False):
        """Kills the watchdog and optionally waits for it to terminate."""
        if self._watchdog is None:
            return

        self._watchdog.shutdown()

        if wait:
            self._watchdog_thread.wait()

        self._watchdog = None
        self._watchdog_thread = None

    def _watchdog_recovery_state_changed(self, watchdog, old_state, new_state):
        """Signal handler called when the recovery state of the watchdog
        changed.
        """
        if new_state:
            # Okay, the watchdog started recovering the connection, so we
            # move to the CONNECTING state
            self._set_state(ConnectionState.CONNECTING)
        else:
            # The watchdog stopped recovering the connection. If the connection
            # is up, we move to the CONNECTED state, otherwise we move to the
            # DISCONNECTED state
            self._set_state(ConnectionState.CONNECTED
                            if self._wrapped.state is ConnectionState.CONNECTED
                            else ConnectionState.DISCONNECTED)

    def __getattr__(self, name):
        """Forwards attribute lookups to the wrapped connection."""
        return getattr(self._wrapped, name)


class ReconnectionWatchdog(object):
    """Watchdog object that holds a weak reference to a connection and tries to
    keep it open even if it is closed.

    The ``run()`` method of this object is typically run in a separate
    thread or greeen thread.
    """

    recovery_state_changed = Signal()

    def __init__(self, connection, lock, retry_interval=1):
        """Constructor.

        :param connection: the connection object
        :type connection: groundctrl.connection.base.Connection
        :param lock: a lock that we must hold whenever we mess around with
            the connection
        :type lock: Lock
        :param retry_interval: number of seconds that must pass betweeen two
            connection attempts
        """
        super(ReconnectionWatchdog, self).__init__()

        self._connection_ref = weakref(connection, self._connection_deleted)
        self._lock = lock
        self.retry_interval = retry_interval

        connection.state_changed.connect(self._on_state_changed, connection)
        self._queue = Queue()

        self._recovering = False

        self.daemon = True

    @property
    def recovering(self):
        """Whether the watchdog is currently trying to recover the
        connection.
        """
        return self._recovering

    @recovering.setter
    def recovering(self, value):
        if self._recovering == value:
            return

        self._recovering = value
        self.recovery_state_changed.send(self, new_state=self._recovering,
                                         old_state=not self._recovering)

    def run(self):
        """Runs the watchdog. The function executes an infinite loop that
        checks the state of the connection and acts according to the following
        simple rules:

            - If the connection is ``DISCONNECTED``, it calls its ``open()``
              method.

            - If the connection is ``CONNECTING`` or ``DISCONNECTING``, it
              does nothing.

            - If the connection changed to ``CONNECTED`` from some other
              state, TODO.

        Then the loop goes to sleep until the state of the connection changes
        or someone requests the watchdog to shut down. This is implemented
        using a message queue. The watchdog subscribes to the ``state_changed``
        signal of the connection and posts a message into the queue (for
        itself) when the state changed. Similarly, other (green) threads may
        call the ``shutdown()`` method of the watchdog to post a message
        into the queue which asks the watchdog to shut down.
        """
        # Before entering the loop, check the current state of the
        # connection. If we are connected, there's nothing to do. If
        # we are disconnected, we have to start a recovery phase.
        # If we are connecting or disconnecting, let's just wait and
        # see how it ends.
        state = self._connection_ref().state
        self._recovering = state is ConnectionState.DISCONNECTED

        while True:
            if self._recovering:
                self._try_to_reopen_connection()

            try:
                message, args = self._queue.get(
                    block=True,
                    timeout=self.retry_interval if self._recovering else None
                )
            except EmptyQueue:
                continue

            try:
                if self._process_message(message, args):
                    break
            finally:
                self._queue.task_done()

        connection = self._connection_ref()
        if connection is not None:
            connection.state_changed.disconnect(self._on_state_changed)
            connection.close()

    def shutdown(self):
        """Shuts down the watchdog thread."""
        self._queue.put(("quit", ()))

    def _connection_deleted(self, ref):
        """Called when the connection watched by this watchdog is about to
        be finalized (i.e. garbage collected).
        """
        self.shutdown()

    def _on_state_changed(self, connection, old_state, new_state):
        """Signal handler called when the state of the connection changed."""
        self._queue.put(("state_changed", (old_state, new_state)))

    def _process_message(self, message, args):
        """Processes a single message from the message queue.

        :return: ``True`` if the watchdog should terminate itself,
            ``False`` otherwise.
        :rtype: bool
        """
        if message == "quit":
            return True
        elif message == "state_changed":
            old_state, new_state = args
            if self.recovering:
                # We are recovering from a connection loss. If the new
                # state is CONNECTED, we have recovered.
                self.recovering = new_state is not ConnectionState.CONNECTED
            else:
                # We are not recovering from a connection loss so maybe
                # we lost the connection now?
                self.recovering = new_state is ConnectionState.DISCONNECTED
        return False

    def _try_to_reopen_connection(self):
        """Tries to reopen the connection associated to the watchdog."""
        connection = self._connection_ref()
        if connection is None:
            return
        with self._lock:
            try:
                connection.open()
            except (IOError, RuntimeError):
                # Swallow any runtime and/or IO errors -- this is a connection
                # failure so we will retry later
                pass


reconnecting = ReconnectionWrapper
