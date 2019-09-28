"""Package that holds classes that implement registries of connections,
UAVs, timers and so on in the server.

Registries map human-readable unique identifiers to the actual business
objects (connections, UAVs, timers and so on). The server will typically
contain a separate registry for each type of object.
"""

from .base import Registry, RegistryBase, find_in_registry
from .channels import ChannelTypeRegistry
from .clients import ClientRegistry
from .clocks import ClockRegistry
from .connections import ConnectionRegistry
from .uavs import UAVRegistry

__all__ = (
    "Registry",
    "RegistryBase",
    "find_in_registry",
    "ClientRegistry",
    "ClockRegistry",
    "ConnectionRegistry",
    "ChannelTypeRegistry",
    "UAVRegistry",
)
