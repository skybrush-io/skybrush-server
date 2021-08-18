"""Central place to manage TCP and UDP ports related to the Skybrush server
and related applications.
"""

from typing import Dict, Optional, Tuple

__all__ = ("get_base_port", "get_port_number_for_service")


#: Base port number. Port numbers of services defined with a relative port
#: number are derived by adding the relative port number to the base port.
BASE_PORT: int = 5000

#: Dictionary mapping registered Skybrush-related services to the corresponding
#: absolute or relative port numbers.
SERVICE_MAP: Dict[str, Tuple[str, int]] = {
    "http": ("relative", 0),
    "tcp": ("relative", 1),
    "udp": ("relative", 1),
    "sidekick": ("relative", 2),
    "rcin": ("relative", 3),
    "ssdp": ("absolute", 1900),
}


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
    try:
        port_type, value = SERVICE_MAP[service]
    except KeyError:
        raise ValueError(f"no such service: {service!r}")

    if port_type == "relative":
        if base_port is None:
            base_port = BASE_PORT
        port = value + base_port
    elif port_type == "absolute":
        # nothing to do
        port = value
    else:
        raise ValueError(f"invalid port type: {port_type!r}")

    return port
