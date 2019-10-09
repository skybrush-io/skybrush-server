"""Generic networking-related utility functions."""

from ipaddress import ip_address, ip_network, IPv6Network
from netifaces import AF_INET, AF_INET6, gateways, ifaddresses, interfaces
from typing import Sequence, Tuple

import socket
import trio.socket

__all__ = (
    "create_socket",
    "find_interfaces_in_network",
    "format_socket_address",
    "get_address_of_network_interface",
    "get_all_ipv4_addresses",
    "get_socket_address",
)


def create_socket(
    socket_type: socket.SocketKind, nonblocking: bool = False
) -> socket.socket:
    """Creates a socket with the given type and performs some administrative
    setup of the socket that makes it easier for us to handle non-graceful
    terminations during development.

    Parameters:
        socket_type: the type of the socket (``socket.SOCK_STREAM`` for
            TCP sockets, ``socket.SOCK_DGRAM`` for UDP sockets)
        nonblocking: whether to make the socket non-blocking

    Returns:
        socket.socket: the newly created socket
    """
    sock = socket.socket(socket.AF_INET, socket_type)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        # Needed on Mac OS X to work around an issue with an earlier
        # instance of the flockctrl process somehow leaving a socket
        # bound to the UDP broadcast address even when the process
        # terminates
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    if nonblocking:
        sock.setblocking(0)
    return sock


def create_async_socket(socket_type) -> trio.socket.socket:
    """Creates an asynchronous socket with the given type.

    Asynchronous sockets have asynchronous sender and receiver methods so
    you need to use the `await` keyword with them.

    Parameters:
        socket_type: the type of the socket (``socket.SOCK_STREAM`` for
            TCP sockets, ``socket.SOCK_DGRAM`` for UDP sockets)

    Returns:
        trio.socket.socket: the newly created socket
    """
    sock = trio.socket.socket(trio.socket.AF_INET, socket_type)
    sock.setsockopt(trio.socket.SOL_SOCKET, trio.socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        # Needed on Mac OS X to work around an issue with an earlier
        # instance of the flockctrl process somehow leaving a socket
        # bound to the UDP broadcast address even when the process
        # terminates
        sock.setsockopt(trio.socket.SOL_SOCKET, trio.socket.SO_REUSEPORT, 1)
    return sock


def find_interfaces_in_network(network: str) -> Sequence[Tuple[str, str, str]]:
    """Finds the network interfaces of the current machine that have at
    least one address that belongs to the given network.

    Parameters:
        network: the network that we are looking for

    Returns:
        for all the network interfaces that have at least one address that
        belongs to the given network, the name of the network interface
        itself, the matched address and the network of the interface, in
        a tuple
    """
    network = ip_network(network)
    if isinstance(network, IPv6Network):
        family = AF_INET6
    else:
        family = AF_INET

    candidates = []
    for interface in interfaces():
        # We are currently interested only in IPv4 addresses
        specs = ifaddresses(interface).get(family) or []
        ip_addresses_in_network = (
            (spec.get("addr"), spec.get("netmask"))
            for spec in specs
            if ip_address(str(spec.get("addr"))) in network
        )
        for address, netmask in ip_addresses_in_network:
            candidates.append(
                (
                    interface,
                    address,
                    str(ip_network(f"{address}/{netmask}", strict=False))
                    if netmask
                    else None,
                )
            )

    return candidates


def format_socket_address(sock, format="{host}:{port}", in_subnet_of=None):
    """Formats the address that the given socket is bound to in the
    standard hostname-port format.

    Parameters:
        sock (socket.socket): the socket to format
        format (str): format string in brace-style that is used by
            ``str.format()``. The tokens ``{host}`` and ``{port}`` will be
            replaced by the hostname and port.
        in_subnet_of (Optional[str,int]): the IP address and port that should
            preferably be in the same subnet as the response. This is used only
            if the socket is bound to all interfaces, in which case we will
            try to pick an interface that is in the same subnet as the remote
            address.

    Returns:
        str: a formatted representation of the address and port of the
            socket
    """
    host, port = get_socket_address(sock, in_subnet_of)
    return format.format(host=host, port=port)


def get_address_of_network_interface(value: str, family: int = AF_INET) -> str:
    """Returns the address of the given network interface in the given
    address family.

    If the interface has multiple addresses, this function returns the first
    one only.

    Parameters:
        value: the name of the network interface
        family: the address family of the interface; one of the `AF_` constants
            from the `netifaces` module

    Returns:
        the address of the given network interface

    Raises:
        ValueError: if the given network interface has no address in the given
            address family
    """
    addresses = ifaddresses(value).get(family)
    if addresses:
        return addresses[0]["addr"]
    else:
        raise ValueError(f"interface {value} has no address")


def get_all_ipv4_addresses():
    """Returns all IPv4 addresses of the current machine."""
    result = []
    for iface in interfaces():
        addresses = ifaddresses(iface)
        if AF_INET in addresses:
            result.append(addresses[AF_INET][0]["addr"])
    return result


def get_broadcast_address_of_network_interface(
    value: str, family: int = AF_INET
) -> str:
    """Returns the broadcast address of the given network interface in the given
    address family.

    Parameters:
        value: the name of the network interface
        family: the address family of the interface; one of the `AF_` constants
            from the `netifaces` module

    Returns:
        the broadcast address of the given network interface

    Raises:
        ValueError: if the given network interface has no broadcast address in
            the given address family
    """
    addresses = ifaddresses(value).get(family)
    if addresses:
        return addresses[0]["broadcast"]
    else:
        raise ValueError(f"interface {value} has no broadcast address")


def get_socket_address(sock, format="{host}:{port}", in_subnet_of=None):
    """Gets the hostname and port that the given socket is bound to.

    Parameters:
        sock (socket.socket): the socket for which we need its address
        in_subnet_of (Optional[str,int]): the IP address and port that should
            preferably be in the same subnet as the response. This is used only
            if the socket is bound to all interfaces, in which case we will
            try to pick an interface that is in the same subnet as the remote
            address.

    Returns:
        Tuple[str, int]: the host and port where the socket is bound to
    """
    if hasattr(sock, "getsockname"):
        host, port = sock.getsockname()
    else:
        host, port = sock

    # Canonicalize the value of 'host'
    if host == "0.0.0.0":
        host = ""

    # If host is empty and an address is given, try to find one from
    # our IP addresses that is in the same subnet as the given address
    if not host and in_subnet_of:
        remote_host, _ = in_subnet_of
        try:
            remote_host = ip_address(remote_host)
        except Exception:
            remote_host = None

        if remote_host:
            for interface in interfaces():
                # We are currently interested only in IPv4 addresses
                specs = ifaddresses(interface).get(AF_INET)
                if not specs:
                    continue
                for spec in specs:
                    if "addr" in spec and "netmask" in spec:
                        net = ip_network(
                            spec["addr"] + "/" + spec["netmask"], strict=False
                        )
                        if remote_host in net:
                            host = spec["addr"]
                            break

        if not host:
            # Try to find the default gateway and then use the IP address of
            # the network interface corresponding to the gateway. This may
            # or may not work; most likely it won't, but that's the best we
            # can do.
            gateway = gateways()["default"][AF_INET]
            if gateway:
                _, interface = gateway
                specs = ifaddresses(interface).get(AF_INET)
                for spec in specs:
                    if "addr" in spec:
                        host = spec["addr"]
                        break

    return host, port
