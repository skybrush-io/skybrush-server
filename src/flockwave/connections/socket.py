"""Connections via TCP or UDP sockets."""

from __future__ import absolute_import, print_function

import struct

from abc import abstractmethod
from ipaddress import ip_address, ip_network, IPv4Network, IPv6Network
from trio import open_tcp_stream, to_thread
from trio.socket import inet_aton, IPPROTO_IP, IP_ADD_MEMBERSHIP, SOCK_DGRAM, SocketType
from typing import Optional, Tuple, Union

from .base import ConnectionBase, ReadableConnection, WritableConnection
from .errors import ConnectionError
from .factory import create_connection
from .stream import StreamConnectionBase
from .types import IPAddressAndPort

from flockwave.server.networking import (
    create_async_socket,
    find_interfaces_in_network,
    get_address_of_network_interface,
    get_broadcast_address_of_network_interface,
)

__all__ = (
    "BroadcastUDPSocketConnection",
    "MulticastUDPSocketConnection",
    "UDPSocketConnection",
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


class SocketConnectionBase(ConnectionBase, InternetAddressMixin):
    """Base class for connection objects using TCP or UDP sockets."""

    def __init__(self):
        ConnectionBase.__init__(self)
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

    async def _close(self):
        """Closes the socket connection."""
        self._socket.close()
        self._socket = None

    @abstractmethod
    async def _create_and_open_socket(self) -> SocketType:
        """Creates and opens the socket that the connection will use."""
        raise NotImplementedError

    async def _open(self):
        """Opens the socket connection."""
        self._socket = await self._create_and_open_socket()

    def _extract_address(self, address):
        """Extracts the *real* IP address and port from the given object.
        The object may be a SocketConnectionBase_, a tuple consisting
        of the IP address and port, or ``None``. Returns a tuple consisting
        of the IP address and port or ``None``.
        """
        if isinstance(address, SocketConnectionBase):
            address = address.address
        return address


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
class UDPSocketConnection(
    SocketConnectionBase,
    ReadableConnection[Tuple[bytes, IPAddressAndPort]],
    WritableConnection[Tuple[bytes, IPAddressAndPort]],
):
    """Connection object that uses a UDP socket."""

    def __init__(self, host: Optional[str] = "", port: int = 0, **kwds):
        """Constructor.

        Parameters:
            host: the IP address or hostname that the socket will bind to. The
                default value means that the socket will bind to all IP
                addresses of the local machine.
            port: the port number that the socket will bind to. Zero means that
                the socket will choose a random ephemeral port number on its
                own.
        """
        super().__init__()
        self._address = (host or "", port or 0)

    async def _create_and_open_socket(self):
        """Creates a new non-blocking reusable UDP socket that is not bound
        anywhere yet.
        """
        sock = create_async_socket(SOCK_DGRAM)
        await self._bind_socket(sock)
        return sock

    async def _bind_socket(self, sock):
        """Binds the given UDP socket to the address where it should listen for
        incoming UDP packets.
        """
        await sock.bind(self._address)

    async def read(self, size: int = 4096, flags: int = 0):
        """Reads some data from the connection.

        Parameters:
            size: the maximum number of bytes to return
            flags: flags to pass to the underlying ``recvfrom()`` call;
                see the UNIX manual for details

        Returns:
            (bytes, tuple): the received data and the address it was
                received from, or ``(b"", None)`` if there was nothing to
                read.
        """
        if self._socket is not None:
            data, addr = await self._socket.recvfrom(size, flags)
            if not data:
                # Remote side closed connection
                await self.close()
            return data, addr
        else:
            return (b"", None)

    async def write(self, data: Tuple[bytes, IPAddressAndPort], flags: int = 0) -> None:
        """Writes the given data to the socket connection.

        Parameters:
            data: the bytes to write, and the address to write the data to
            flags: additional flags to pass to the underlying ``send()``
                or ``sendto()`` call; see the UNIX manual for details.
        """
        if self._socket is not None:
            data, address = data
            await self._socket.sendto(data, flags, address)
        else:
            raise RuntimeError("connection does not have a socket")


@create_connection.register("udp-broadcast")
class BroadcastUDPSocketConnection(UDPSocketConnection):
    """Connection object that binds to the broadcast address of a given
    subnet or a given interface.
    """

    def __init__(self, interface=None, port=0, **kwds):
        """Constructor.

        Parameters:
            interface (str): name of the network interface whose broadcast
                address to bind to, or a subnet in slashed notation whose
                broadcast address to bind to
            port (int): the port number that the socket will bind (or
                connect) to. Zero means that the socket will choose a random
                ephemeral port number on its own.

        Keyword arguments:
            path (str): convenience alias for `interface` so we can use this class
                with `create_connection.register()`
        """
        interface = interface or kwds.get("path")

        if interface is None:
            address = "255.255.255.255"
        else:
            try:
                network = ip_network(interface)
                address = str(network.broadcast_address)
            except ValueError:
                # Not an IPv4 network in slashed notation; try it as an
                # interface name
                address = get_broadcast_address_of_network_interface(interface)

        super().__init__(host=address, port=port)


@create_connection.register("udp-multicast")
class MulticastUDPSocketConnection(UDPSocketConnection):
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

    async def _create_and_open_socket(self):
        """Creates a new non-blocking reusable UDP socket that is not bound
        anywhere yet.
        """
        sock = await super()._create_and_open_socket()

        address = await to_thread.run_sync(self._resolve_interface, self._interface)

        host, _ = self._address
        req = struct.pack("4s4s", inet_aton(host), inet_aton(address))
        sock.setsockopt(IPPROTO_IP, IP_ADD_MEMBERSHIP, req)

        return sock

    @staticmethod
    def _resolve_interface(value: Optional[str]) -> str:
        """Takes the name of a network interface or an IP address as input,
        and returns the resolved and validated IP address.

        This process might call `netifaces.ifaddresses()` et al in the
        background, which could potentially be blocking. It is advised to run
        this function in a separate worker thread.

        Parameters:
            value: the IP address to validate, or the interface whose IP address
                we are about to retrieve. `None` means that we are binding to
                all interfaces.

        Returns:
            the IPv4 address of the interface.
        """
        try:
            return str(ip_address(value)) if value else "0.0.0.0"
        except ValueError:
            return str(get_address_of_network_interface(value))


@create_connection.register("udp-subnet")
class SubnetBindingUDPSocketConnection(UDPSocketConnection):
    """Connection object that enumerates the IP addresses of the network
    interfaces and creates a UDP or TCP socket connection bound to the
    network interface that is within a given subnet.

    If there are multiple network interfaces that match the given subnet,
    the connection binds to the first one it finds.
    """

    def __init__(
        self,
        network: Optional[Union[IPv4Network, IPv6Network, str]] = None,
        port: int = 0,
        **kwds
    ):
        """Constructor.

        Parameters:
            network (Union[IPv4Network, IPv6Network, str]): an IPv4 or IPv6 network
                object that describes the subnet that the connection tries to bind
                to, or its string representation
            port (int): the port number to which the newly created sockets will
                be bound to. Zero means to pick an ephemeral port number
                randomly.

        Keyword arguments:
            path (str): convenience alias for `network` so we can use this class
                with `create_connection.register()`
        """

        if network is None:
            network = kwds.get("path")
            if network is None:
                raise ValueError("either 'network' or 'path' must be given")

        super().__init__(port=port)

        self._network = ip_network(network)

    async def _bind_socket(self, sock):
        """Binds the given UDP socket to the address where it should listen for
        incoming UDP packets.
        """
        interfaces = find_interfaces_in_network(self._network)
        if not interfaces:
            raise ConnectionError("no network interface in the given network")

        self._address = (interfaces[0][1], self._address[1])
        return await super()._bind_socket(sock)


async def test_udp():
    sender = UDPSocketConnection("127.0.0.1")
    receiver = UDPSocketConnection("127.0.0.1")

    try:
        await sender.open()
        await receiver.open()

        assert await sender.write(b"helo", receiver) == 4
        data, address = await receiver.read()
        assert data == b"helo"
        assert address == sender.address
    finally:
        await sender.close()
        await receiver.close()


if __name__ == "__main__":
    import sys
    import trio

    sys.exit(trio.run(test_udp))
