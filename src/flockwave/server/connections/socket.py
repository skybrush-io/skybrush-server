"""Connections via TCP or UDP sockets."""

from __future__ import absolute_import, print_function

import struct

from blinker import Signal
from builtins import str
from contextlib import closing
from errno import EAGAIN
from functools import partial
from ipaddress import ip_address, IPv4Network
from trio import open_tcp_stream, socket

from .base import FDConnectionBase, ConnectionState, ConnectionWrapperBase
from .factory import create_connection
from .stream import StreamConnectionBase

from ..networking import create_socket


__all__ = (
    "UDPSocketConnection",
    "MulticastUDPSocketConnection",
    "SubnetBindingConnection",
    "SubnetBindingUDPConnection",
    "TCPStreamConnection",
)


class InternetAddressMixin:
    """Mixin class that adds an "address" property to a connection, consisting
    of an IP address and a port."""

    def __init__(self):
        self._address = None

    @property
    def address(self):
        """Returns the IP address and port of the socket, in the form of a
        tuple.
        """
        return self._address

    @property
    def ip(self):
        """Returns the IP address that the socket is bound to."""
        return self.address[0]

    @property
    def port(self):
        """Returns the port that the socket is bound to."""
        return self.address[1]


class SocketConnectionBase(FDConnectionBase, InternetAddressMixin):
    """Base class for connection objects using TCP or UDP sockets."""

    def __init__(self):
        FDConnectionBase.__init__(self)
        InternetAddressMixin.__init__(self)
        self._socket = None

    @InternetAddressMixin.address.getter
    def address(self):
        """Returns the IP address and port of the socket, in the form of a
        tuple.
        """
        if self._socket is None:
            # No socket yet; try to obtain the address from the "_address"
            # property instead
            if self._address is None:
                raise ValueError("socket is not open yet")
            else:
                return super().address
        else:
            # Ask the socket for its address
            return self._socket.getsockname()

    @property
    def socket(self):
        """Returns the socket object itself."""
        return self._socket

    def _set_socket(self, value):
        """Protected setter for the socket object. Derived classes should
        not modify ``_socket`` directly but use ``_set_socket()`` instead.
        """
        if self._socket == value:
            return

        self._socket = value
        self._attach(value.makefile() if value else None)

    def _test_socket_error(self):
        """Returns whether the last operation performed on this socket
        ended with an error core or not.

        Returns:
            int: the error code of the last socket operation or zero if the
                last operation was successful
        """
        return self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)


class InternetSocketConnection(SocketConnectionBase):
    """Base class for the standard Internet socket connections
    (TCP and UDP).
    """

    def __init__(self, host="", port=0, **kwds):
        """Constructor.

        Parameters:
            host (Optional[str]): the IP address or hostname that the socket
                will bind (or connect) to. The default value means that the
                socket will bind to all IP addresses of the local machine.
            port (int): the port number that the socket will bind (or
                connect) to. Zero means that the socket will choose a random
                ephemeral port number on its own.
        """
        super(InternetSocketConnection, self).__init__()
        self._address = (host or "", port or 0)

    async def _close(self):
        """Closes the socket connection."""
        self._socket.close()
        self._set_socket(None)

    async def _open(self):
        """Opens the socket connection."""
        self._set_socket(await self._create_and_open_socket())

    def _on_connected(self):
        self._set_state(ConnectionState.CONNECTED)

    def read(self, size=4096, flags=0):
        """Reads some data from the connection.

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
        if self._socket is not None:
            try:
                data, addr = self._socket.recvfrom(size, flags)
                if not data:
                    # Remote side closed connection
                    self.close()
                return data, addr
            except socket.error as ex:
                if ex.errno == EAGAIN:
                    return (b"", None)
                else:
                    self._handle_error()
            except Exception:
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
                value. Returns -1 if there was an error while sending the
                data.
        """
        if self._socket is not None:
            address = self._extract_address(address)
            socket = self._socket
            try:
                if address is None:
                    return socket.send(data, flags)
                else:
                    return socket.sendto(data, flags, address)
            except Exception:
                self._handle_error()
                return -1
        else:
            return -1

    def _extract_address(self, address):
        """Extracts the *real* IP address and port from the given object.
        The object may be an InternetSocketConnection_, a tuple consisting
        of the IP address and port, or ``None``. Returns a tuple consisting
        of the IP address and port or ``None``.
        """
        if isinstance(address, InternetSocketConnection):
            address = address.address
        return address

    def _open_internal(self, notify_connected):
        """Implementation of ``self.open()`` without the common administrative
        stuff; to be overridden in subclasses.

        Parameters:
            notify_connected (callable): function that this function must
                call when the connection was opened
        """
        raise NotImplementedError


@create_connection.register("tcp")
class TCPStreamConnection(StreamConnectionBase, InternetAddressMixin):
    """Connection object that wraps a Trio TCP stream."""

    def __init__(self, host="", port=0, **kwds):
        """Constructor.

        Parameters:
            host (Optional[str]): the IP address or hostname that the socket
                will bind (or connect) to. The default value means that the
                socket will bind to all IP addresses of the local machine.
            port (int): the port number that the socket will bind (or
                connect) to. Zero means that the socket will choose a random
                ephemeral port number on its own.
        """
        StreamConnectionBase.__init__(self)
        InternetAddressMixin.__init__(self)
        self._address = (host or "", port or 0)

    async def _create_stream(self):
        """Creates a new non-blocking reusable TCP socket and connects it to
        the target of the connection.
        """
        host, port = self._address
        return await open_tcp_stream(host, port)


@create_connection.register("udp")
class UDPSocketConnection(InternetSocketConnection):
    """Connection object that uses a UDP socket."""

    def _create_socket(self):
        """Creates a new non-blocking reusable UDP socket that is not bound
        anywhere yet.
        """
        return create_socket(socket.SOCK_DGRAM, nonblocking=True)

    def _open_internal(self, notify_connected):
        """Implementation of ``self.open()`` without the common administrative
        stuff; to be overridden in subclasses.
        """
        self._socket.bind(self._address)
        notify_connected()


@create_connection.register("udp-multicast")
class MulticastUDPSocketConnection(InternetSocketConnection):
    """Connection object that uses a multicast UDP socket."""

    def __init__(self, group=None, port=0, interface=None, **kwds):
        """Constructor.

        Parameters:
            group (str): the IP address of the multicast group that the socket
                will bind to.
            port (int): the port number that the socket will bind (or
                connect) to. Zero means that the socket will choose a random
                ephemeral port number on its own.
            interface (Optional[str]): name of the network interface to bind
                the socket to. `None` means to bind to the default network
                interface where multicast is supported.

        Keyword arguments:
            host (str): convenience alias for `group` so we can use this class
                with `create_connection.register()`
        """
        if group is None:
            group = kwds.get("host")
            if group is None:
                raise ValueError("either 'group' or 'host' must be given")

        if not ip_address(group).is_multicast:
            raise ValueError("expected multicast group address")

        super().__init__(host=group, port=port)

        self._interface = interface

    def _create_socket(self):
        """Creates a new non-blocking reusable UDP socket that is not bound
        anywhere yet.
        """
        return create_socket(socket.SOCK_DGRAM, nonblocking=True)

    def _get_interface_address(self):
        """Returns the IP address of the interface that the socket wishes to
        bind to.
        """
        if not self._interface:
            return "0.0.0.0"

        try:
            return str(ip_address(self._interface))
        except ValueError:
            # address is not an IP address
            pass

        import netifaces  # lazy import

        addresses = netifaces.ifaddresses(self._interface).get(netifaces.AF_INET)
        if addresses:
            return addresses[0]["addr"]
        else:
            raise ValueError(
                "interface {0} has no IPv4 address".format(self._interface)
            )

    def _open_internal(self, notify_connected):
        self._socket.bind(self._address)

        host, _ = self._address
        req = struct.pack(
            "4s4s",
            socket.inet_aton(host),
            socket.inet_aton(self._get_interface_address()),
        )
        self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, req)

        notify_connected()


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

    def __init__(self, network, connection_factory, port=0, bind_to_broadcast=False):
        """Constructor."""
        super(SubnetBindingConnection, self).__init__()
        self.connection_factory = connection_factory
        self.port = port
        self.bind_to_broadcast = bind_to_broadcast

        import netifaces  # lazy import

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
        """Opens the WLAN socket connection."""
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
            candidates.extend(
                spec[addr_key]
                for spec in specs
                if ip_address(str(spec.get("addr"))) in self._network
                and addr_key in spec
            )

            # If we have more than one candidate, we can safely exit here
            if len(candidates) > 1:
                return None

        # If we have exactly one IP address candidate in the target subnet,
        # return this IP address -- unless it's localhost, in which case
        # we throw an exception because broadcasts are not supported on
        # localhost; one should use multicast instead
        if candidates:
            if ip_address(candidates[0]).is_loopback:
                raise ValueError("broadcast not supported on localhost")
            else:
                return candidates[0]
        else:
            return None  # return this IP address

    def _update_own_state_from_wrapped_connection(self):
        """Updates the state of the current connection based on the state
        of the wrapper.
        """
        new_state = (
            self._wrapped.state if self._wrapped else ConnectionState.DISCONNECTED
        )
        self._set_state(new_state)

    def _wrapped_connection_changed(self, old_conn, new_conn):
        if old_conn:
            old_conn.state_changed.disconnect(
                self._wrapped_connection_state_changed, sender=old_conn
            )

        if new_conn:
            new_conn.state_changed.connect(
                self._wrapped_connection_state_changed, sender=new_conn
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


SubnetBindingUDPBroadcastConnection = partial(
    SubnetBindingUDPConnection, bind_to_broadcast=True
)
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
