"""Central place to manage TCP and UDP ports related to the Skybrush server
and related applications.
"""

from contextlib import contextmanager
from types import MappingProxyType
from typing import Iterator, Literal, Mapping

from deprecated import deprecated

__all__ = ("get_base_port", "suggest_port_number_for_service")


PortSpec = tuple[Literal["relative", "absolute"], int]
"""Typing for a single entry in the `SERVICE_MAP` dictionary that tells how to
determine the preferred port of a service.
"""

BASE_PORT: int = 5000
"""Base port number. Port numbers of services defined with a relative port
number are derived by adding the relative port number to the base port.
"""

SERVICE_MAP: dict[str, PortSpec] = {
    "http": ("relative", 0),
    "tcp": ("relative", 1),
    "udp": ("relative", 1),
    "sidekick": ("relative", 2),
    "rcin": ("relative", 3),
    "ssdp": ("absolute", 1900),
}
"""Dictionary mapping registered Skybrush-related services to the corresponding
suggested absolute or relative port numbers.
"""

_BASE_PORT_USED: bool = False
"""Stores whether the base port was already used by ``suggest_port_number_for_service()``
to derive the port number of a service that uses a relative port number.
"""

_registered_ports: dict[str, int] = {}
"""Dictionary mapping the ports _actually_ registered by extensions for
individual services.

Extensions are supposed to use the `use_port()` context manager to register a
port in this dictionary.
"""


def get_base_port() -> int:
    """Returns the base port that is used to derive port numbers for services
    with relative port numbers.
    """
    return BASE_PORT


def get_port_map() -> Mapping[str, int]:
    """Returns a mapping from currently registered service names to the port
    numbers they use.
    """
    return MappingProxyType(_registered_ports)


@deprecated(reason="use suggest_port_number_for_service")
def get_port_number_for_service(service: str, base_port: int | None = None) -> int:
    return suggest_port_number_for_service(service, base_port)


def suggest_port_number_for_service(service: str, base_port: int | None = None) -> int:
    """Returns a suggested port number for the given Skybrush-related service.

    Service names are keys in the `SERVICE_MAP` dictionary. Typical service
    names are: `http` for the HTTP (WebSocket) based communication channel,
    `tcp` for TCP streams, `udp` for UDP packets, `ssdp` for Simple Service
    Discovery Protocol etc.

    Parameters:
        service: the name of the service whose port number is to be retrieved
        base_port: the base port to use if the service is defined in terms of a
            relative port number that must be added to a base port; ignored for
            services that are defined with absolute port numbers. `None` means
            to use the default base port from the `BASE_PORT` variable.

    Returns:
        the suggested port number

    Raises:
        ValueError: if the service is not known in the service map
    """
    global _BASE_PORT_USED

    try:
        port_type, value = SERVICE_MAP[service]
    except KeyError:
        raise ValueError(f"no such service: {service!r}") from None

    if port_type == "relative":
        if base_port is None:
            base_port = get_base_port()
            _BASE_PORT_USED = True
        port = value + base_port
    elif port_type == "absolute":
        # nothing to do
        port = value
    else:
        raise ValueError(f"invalid port type: {port_type!r}")

    return port


def set_base_port(value: int) -> None:
    """Sets the base port of the server. This must be done early during the
    startup process, _before_ ``suggest_port_number_for_service()`` is invoked
    for any service that uses a relative port number.

    Raises:
        RuntimeError: whent trying to set the base port after the old value was
            already used for deriving the port number of a service that uses a
            relative port number
    """
    global BASE_PORT, _BASE_PORT_USED

    if _BASE_PORT_USED:
        raise RuntimeError(
            "base port cannot be set as it was already used to derive the "
            "port number of a service"
        )

    if value <= 0 or value >= 32768:
        raise RuntimeError(
            "invalid port number; must be positive and smaller than 32768"
        )

    BASE_PORT = value


@contextmanager
def use_port(service: str, port: int) -> Iterator[None]:
    """Context manager that registers a port as being used by a service."""
    global _registered_ports

    if service in _registered_ports:
        raise RuntimeError(f"service already registered: {service!r}")

    _registered_ports[service] = port
    try:
        yield
    finally:
        _registered_ports.pop(service, None)
