"""Connections via TCP or UDP sockets."""

from __future__ import absolute_import, print_function

import csv
import socket

from blinker import Signal
from builtins import str
from contextlib import closing
from errno import EAGAIN
from functools import partial
from ipaddress import IPv4Address, IPv4Network
from select import select
from time import time

from .base import FDConnectionBase, ConnectionState, ConnectionWrapperBase
from .factory import create_connection

__all__ = ("UDPSocketConnection", "SubnetBindingConnection",
           "SubnetBindingUDPConnection")


class SocketConnectionBase(FDConnectionBase):
    """Base class for connection objects using TCP or UDP sockets."""

    def __init__(self):
        super(SocketConnectionBase, self).__init__()
        self._event_loop = None
        self._fd_event_loop_handle = None
        self._socket = None

    def _create_socket(self):
        """Creates a new socket for the connection. Must be overridden in
        subclasses.
        """
        raise NotImplementedError

    @property
    def address(self):
        """Returns the address (IP and port) of the socket."""
        if self._socket is None:
            raise ValueError("socket is not open yet")
        return self._socket.getsockname()

    @property
    def ip(self):
        """Returns the IP address that the socket is bound to."""
        return self.address[0]

    @property
    def port(self):
        """Returns the port that the socket is bound to."""
        return self.address[1]

    @property
    def socket(self):
        """Returns the socket object itself."""
        return self._socket

    def _set_event_loop(self, value):
        """Registers the socket in an urwid event loop or unregisters it
        from an event loop.

        Parameters:
            value (Optional[urwid.MainLoop]): the urwid event loop to
                register the socket in, or ``None`` if the socket is to be
                unregistered from the current event loop
        """
        if self._event_loop == value:
            return

        fd_registered = self._fd_event_loop_handle is not None
        if self._event_loop and fd_registered:
            self._event_loop.remove_watch_file(self._fd_event_loop_handle)

        self._event_loop = value

        if self._event_loop and fd_registered:
            fd = self.fileno()
            if fd:
                self._fd_event_loop_handle = self._event_loop.watch_file(
                    fd, self._on_socket_readable)

    def _set_socket(self, value):
        """Protected setter for the socket object. Derived classes should
        not modify ``_socket`` directly but use ``_set_socket()`` instead.
        """
        if self._socket == value:
            return

        self._socket = value
        self._attach(value.makefile() if value else None)

    def register_in_event_loop(self, loop):
        """Registers the socket connection in the given urwid event loop."""
        self._set_event_loop(loop)

    def unregister_from_event_loop(self):
        """Unregisters the DGPS connection from the current urwid
        event loop.
        """
        self._set_event_loop(None)

    def wait_until_readable(self):
        """Blocks the current thread until the socket becomes readable."""
        while True:
            rlist, _, _ = select([self], [], [])
            if rlist:
                break

    def _on_socket_readable(self):
        """Handler called by the urwid event loop if the socket became
        readable.
        """
        # TODO
        pass


@create_connection.register("udp")
class UDPSocketConnection(SocketConnectionBase):
    """Connection object that uses an UDP socket."""

    def __init__(self, ip_address="", port=0):
        """Constructor.

        Parameters:
            ip_address (Optional[str]): the IP address that the socket will
                bind to. The default value means that the socket will bind
                to all IP addresses of the local machine. May also be a
                broadcast address.
            port (int): the port number that the socket will bind to. Zero
                means that the socket will choose a random ephemeral port
                number on its own.
        """
        super(UDPSocketConnection, self).__init__()
        self._port = port or 0
        self._ip_address = ip_address or ""

    def _create_socket(self):
        """Creates a new non-blocking reusable UDP socket that is not bound
        anywhere yet.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(0)
        return sock

    def close(self):
        """Closes the WLAN socket connection."""
        if self.state == ConnectionState.DISCONNECTED:
            return

        self._socket.close()
        self._set_socket(None)
        self._set_state(ConnectionState.DISCONNECTED)

    def open(self):
        """Opens the WLAN socket connection."""
        if self.state == ConnectionState.CONNECTED:
            return

        self._set_socket(self._create_socket())
        self._socket.bind((self._ip_address, self._port))
        self._set_state(ConnectionState.CONNECTED)

    def read(self, size=4096, flags=0, blocking=False):
        """Reads some data UDP datagram from the connection.

        Parameters:
            size (int): the maximum number of bytes to return
            flags (int): flags to pass to the underlying ``recvfrom()`` call;
                see the UNIX manual for details
            blocking (bool): whether to use a blocking read (even if the
                underlying socket is non-blocking). This is not thread-safe
                yet, i.e. multiple blocking reads from different threads
                might result in conditions where more than one thread is
                woken up but only one of them gets to read the socket.

        Returns:
            (bytes, tuple): the received data and the address it was
                received from, or ``(b"", None)`` if there was nothing to
                read.
        """
        if blocking:
            self.wait_until_readable()
        if self._socket is not None:
            try:
                return self._socket.recvfrom(size, flags)
            except socket.error as ex:
                if ex.errno == EAGAIN:
                    return (b"", None)
                else:
                    self._handle_error()
            except:
                self._handle_error()
        return (b"", None)

    def write(self, data, address=None, flags=0):
        """Writes the given data to the socket connection.

        Parameters:
            data (bytes): the bytes to write
            address (Optional[tuple]): the address to write the data to;
                ``None`` means to write the data to wherever the socket is
                currently connected. The latter option works only if the
                socket was explicitly connected to an address beforehand
                with the ``connect()`` method.
            flags (int): additional flags to pass to the underlying ``send()``
                or ``sendto()`` call; see the UNIX manual for details.

        Returns:
            int: the number of bytes successfully written through the
                socket. Note that it may happen that some of the data was
                not written; you are responsible for checking the return
                value.
        """
        if self._socket is not None:
            try:
                if address is None:
                    return self._socket.send(data, flags)
                if isinstance(address, SocketConnectionBase):
                    address = address.address
                return self._socket.sendto(data, flags, address)
            except:
                self._handle_error()
        else:
            return 0


class SubnetBindingConnection(ConnectionWrapperBase):
    """Connection object that enumerates the IP addresses of the network
    interfaces and creates a UDP or TCP socket connection bound to the
    network interface that is within a given subnet, assuming that there is
    only one such network interface.

    Attributes:
        network (Union[IPv4Network,str]): an IPv4 network object that
            describes the subnet that the connection tries to bind to, or
            its string representation
        connection_factory (callable): callable object that returns a
            new SocketConnectionBase_ instance when invoked with an IP
            address and a port number. This determines whether the wrapper
            will create TCP or UDP connections.
        port (int): the port number to which the newly created sockets will
            be bound to. Zero means to pick an ephemeral port number
            randomly.
        bind_to_broadcast (bool): whether to bind to the broadcast address
            of the network interface (True) or the own IP address of the
            network interface (False)
    """

    file_handle_changed = Signal()

    def __init__(self, network, connection_factory, port=0,
                 bind_to_broadcast=False):
        """Constructor."""
        super(SubnetBindingConnection, self).__init__()
        self.connection_factory = connection_factory
        self.port = port
        self.bind_to_broadcast = bind_to_broadcast

        import netifaces             # lazy import
        self._netifaces = netifaces

        if isinstance(network, IPv4Network):
            self._network = network
        else:
            self._network = IPv4Network(str(network))

    def close(self):
        """Closes the WLAN socket connection."""
        if self._wrapped is not None:
            self._wrapped.close()
            self._set_wrapped(None)

    def open(self):
        # If we have no wrapped connection yet, create one and then try to
        # open it. Our state will follow the state of the wrapped connection.
        if self._wrapped is None:
            address = self._find_ip_address_in_subnet()
            if address is None:
                return

            self._set_wrapped(self.connection_factory(address, self.port))

        self._wrapped.open()

    def _find_ip_address_in_subnet(self):
        """Finds an IP address among the addresses of all the network
        interfaces of the current machine that belongs to the subnet.
        """
        candidates = []
        addr_key = "broadcast" if self.bind_to_broadcast else "addr"
        for interface in self._netifaces.interfaces():
            # We are currently interested only in IPv4 addresses
            specs = self._netifaces.ifaddresses(interface).get(self._netifaces.AF_INET)
            if not specs:
                continue

            # Find only those addresses that are in our target subnet
            candidates.extend(spec[addr_key] for spec in specs
                if IPv4Address(str(spec.get("addr"))) in self._network \
                and addr_key in spec)

            # If we have more than one candidate, we can safely exit here
            if len(candidates) > 1:
                return None

        # If we have exactly one IP address candidate in the target subnet,
        # return this IP address
        return candidates[0] if candidates else None

    def _update_own_state_from_wrapped_connection(self):
        """Updates the state of the current connection based on the state
        of the wrapper.
        """
        new_state = self._wrapped.state if self._wrapped else ConnectionState.DISCONNECTED
        self._set_state(new_state)

    def _wrapped_connection_changed(self, old_conn, new_conn):
        if old_conn:
            old_conn.state_changed.disconnect(
                self._wrapped_connection_state_changed,
                sender=old_conn
            )

        if new_conn:
            new_conn.state_changed.connect(
                self._wrapped_connection_state_changed,
                sender=new_conn
            )

        self._update_own_state_from_wrapped_connection()

    def _wrapped_connection_state_changed(self, sender, old_state, new_state):
        """Handler that is called when the state of the wrapped connection
        changes.
        """
        self._update_own_state_from_wrapped_connection()


@create_connection.register("udp-subnet")
def SubnetBindingUDPConnection(subnet=None, port=0, bind_to_broadcast=False, **kwds):
    """Convenience factory for a SubnetBindingConnection_ that works with
    UDP sockets.

    Parameters:
        subnet (Union[IPv4Network,str]): the IPv4 network within which the
            connection will try to find an appropriate network interface
        port (int): the port to bind the UDP socket to
        bind_to_broadcast (bool): whether to bind to the broadcast address
            of the network interface (True) or the own IP address of the
            network interface (False)

    Keyword arguments:
        path: alias to ``subnet`` so we can use the argument in a
            ConnectionFactory_ in a more natural way (for example,
            ``udp-subnet:192.168.1.0/24``)
    """
    if subnet is None:
        subnet = kwds.get("path")
        if subnet is None:
            raise ValueError("either 'subnet' or 'path' must be given")
    return SubnetBindingConnection(subnet, UDPSocketConnection, port, bind_to_broadcast)


SubnetBindingUDPBroadcastConnection = partial(SubnetBindingUDPConnection,
                                              bind_to_broadcast=True)
create_connection.register("udp-broadcast", SubnetBindingUDPBroadcastConnection)


def test_udp():
    with closing(UDPSocketConnection("127.0.0.1")) as sender:
        with closing(UDPSocketConnection("127.0.0.1")) as receiver:
            sender.open()
            receiver.open()

            assert sender.write(b"helo", receiver) == 4
            data, address = receiver.read(blocking=True)
            assert data == b"helo"
            assert address == sender.address


if __name__ == "__main__":
    import sys
    sys.exit(test_udp())
