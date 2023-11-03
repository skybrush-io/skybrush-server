"""Central place to manage TCP and UDP ports related to the Skybrush server
and related applications.
"""

from typing import Optional

__all__ = ("get_base_port", "get_port_number_for_service")


BASE_PORT: int = 5000
"""Base port number. Port numbers of services defined with a relative port
number are derived by adding the relative port number to the base port.
"""

SERVICE_MAP: dict[str, tuple[str, int]] = {
    "http": ("relative", 0),
    "tcp": ("relative", 1),
    "udp": ("relative", 1),
    "sidekick": ("relative", 2),
    "rcin": ("relative", 3),
    "ssdp": ("absolute", 1900),
}
"""Dictionary mapping registered Skybrush-related services to the corresponding
absolute or relative port numbers.
"""

_BASE_PORT_USED: bool = False
"""Stores whether the base port was already used by ``get_port_number_for_service()``
to derive the port number of a service that uses a relative port number.
"""


def get_base_port() -> int:
    """Returns the base port that is used to derive port numbers for services
    with relative port numbers.
    """
    return BASE_PORT


def get_port_number_for_service(service: str, base_port: Optional[int] = None) -> int:
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
    startup process, _before_ ``get_port_number_for_service()`` is invoked
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
