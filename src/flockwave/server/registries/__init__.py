"""Package that holds classes that implement registries of connections,
UAVs, timers and so on in the server.

Registries map human-readable unique identifiers to the actual business
objects (connections, UAVs, timers and so on). The server will typically
contain a separate registry for each type of object.
"""

from .base import Registry, RegistryBase, find_in_registry
from .channels import ChannelTypeRegistry
from .clients import ClientRegistry
from .connections import ConnectionRegistry, ConnectionRegistryEntry
from .objects import ObjectRegistry
from .uav_drivers import UAVDriverRegistry
from .weather import WeatherProviderRegistry

__all__ = (
    "Registry",
    "RegistryBase",
    "find_in_registry",
    "ClientRegistry",
    "ConnectionRegistry",
    "ConnectionRegistryEntry",
    "ChannelTypeRegistry",
    "ObjectRegistry",
    "UAVDriverRegistry",
    "WeatherProviderRegistry",
)
