"""Model-related classes for the Flockwave server."""

from __future__ import absolute_import

from .builders import FlockwaveMessageBuilder
from .connection import ConnectionPurpose, ConnectionInfo, ConnectionStatus
from .messages import FlockwaveMessage, FlockwaveNotification, \
    FlockwaveResponse
from .registry import RegistryBase, Registry
from .uav import UAVStatusInfo
from .vectors import GPSCoordinate, FlatEarthCoordinate, \
    FlatEarthToGPSCoordinateTransformation


__all__ = (
    "FlockwaveMessage", "FlockwaveMessageBuilder", "FlockwaveNotification",
    "FlockwaveResponse", "GPSCoordinate", "UAVStatusInfo",
    "FlatEarthCoordinate", "FlatEarthToGPSCoordinateTransformation",
    "RegistryBase", "Registry",
    "ConnectionInfo", "ConnectionPurpose", "ConnectionStatus"
)
